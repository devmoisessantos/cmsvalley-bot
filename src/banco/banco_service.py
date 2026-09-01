"""
Acesso ao banco para o painel administrativo.

Só opera sobre tabelas registradas em ``models.Base.metadata``.
Nunca monta SQL com texto livre do membro.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Table,
    and_,
    delete,
    func,
    or_,
    select,
)

from src.database.conexao import async_session
from src.database.models import Base

# Quantas linhas por página no painel
LINHAS_POR_PAGINA = 10

# Colunas mais usadas para busca rápida (ordem de preferência no resumo)
COLUNAS_BUSCA_COMUM = (
    "discord_id",
    "id_fivem",
    "discord_id_candidato",
    "discord_id_recrutador",
    "discord_id_psicologo",
    "discord_id_paciente",
    "id_fivem_psicologo",
    "id_fivem_paciente",
    "executor_id",
    "outro_discord_id",
    "autor_discord_id",
    "nickname_atual",
    "status",
    "cargo",
    "nome_painel",
)


def listar_nomes_das_tabelas() -> list[str]:
    """
    Nomes de tabela conhecidos pelo bot, em ordem alfabética.
    """
    return sorted(Base.metadata.tables.keys())


def tabela_e_conhecida(nome_da_tabela: str) -> bool:
    """True se a tabela existe no metadata do projeto."""
    return nome_da_tabela in Base.metadata.tables


def obter_tabela(nome_da_tabela: str) -> Table:
    """
    Devolve o objeto Table do metadata.

    Levanta KeyError se o nome não for do projeto.
    """
    if not tabela_e_conhecida(nome_da_tabela):
        raise KeyError(f"Tabela desconhecida: {nome_da_tabela}")
    return Base.metadata.tables[nome_da_tabela]


def listar_colunas(nome_da_tabela: str) -> list[str]:
    """Nomes das colunas na ordem do model."""
    tabela = obter_tabela(nome_da_tabela)
    return [coluna.name for coluna in tabela.columns]


def listar_chaves_primarias(nome_da_tabela: str) -> list[str]:
    """Colunas que formam a chave primária."""
    tabela = obter_tabela(nome_da_tabela)
    return [coluna.name for coluna in tabela.primary_key.columns]


def listar_colunas_buscaveis(nome_da_tabela: str) -> list[str]:
    """
    Colunas úteis para filtro no painel.

    Prioriza IDs e campos comuns; inclui o restante depois.
    """
    todas = listar_colunas(nome_da_tabela)
    prioritarias = [c for c in COLUNAS_BUSCA_COMUM if c in todas]
    restantes = [c for c in todas if c not in prioritarias]
    return prioritarias + restantes


def _formatar_celula(valor: Any) -> str:
    """Texto curto para caber no select / card."""
    if valor is None:
        return "∅"
    texto = str(valor)
    if len(texto) > 40:
        return texto[:37] + "..."
    return texto


def _converter_valor_filtro(tabela: Table, nome_do_campo: str, valor_bruto: str) -> Any:
    """
    Converte o texto digitado no filtro para o tipo da coluna.
    """
    bruto = (valor_bruto or "").strip()
    if bruto.lower() in ("null", "none", "∅"):
        return None

    coluna = tabela.c[nome_do_campo]
    tipo_python = (
        coluna.type.python_type if hasattr(coluna.type, "python_type") else str
    )

    if tipo_python is bool:
        texto = bruto.lower()
        if texto in ("1", "true", "sim", "yes", "s", "v"):
            return True
        if texto in ("0", "false", "nao", "não", "no", "n", "f"):
            return False
        raise ValueError(f"Valor booleano inválido para `{nome_do_campo}`.")

    if tipo_python is int:
        return int(bruto)

    if tipo_python is float:
        return float(bruto.replace(",", "."))

    return bruto


def _montar_condicao_filtro(
    tabela: Table,
    filtros: dict[str, str] | None,
    busca_livre: str | None,
):
    """
    Monta cláusula WHERE a partir de filtros por coluna e/ou busca livre.

    - filtros: {coluna: valor_texto} → igualdade tipada
    - busca_livre: tenta casar o texto em colunas de ID comuns e em PKs;
      se for só dígitos, compara como int nas colunas numéricas
    """
    condicoes = []

    if filtros:
        for nome_coluna, valor_texto in filtros.items():
            if nome_coluna not in tabela.c:
                continue
            if valor_texto is None or str(valor_texto).strip() == "":
                continue
            valor = _converter_valor_filtro(tabela, nome_coluna, str(valor_texto))
            if valor is None:
                condicoes.append(tabela.c[nome_coluna].is_(None))
            else:
                condicoes.append(tabela.c[nome_coluna] == valor)

    if busca_livre and busca_livre.strip():
        termo = busca_livre.strip()
        candidatos = []
        # Colunas prioritárias + PK
        nomes_prioritarios = [c for c in COLUNAS_BUSCA_COMUM if c in tabela.c]
        for col_pk in tabela.primary_key.columns:
            if col_pk.name not in nomes_prioritarios:
                nomes_prioritarios.append(col_pk.name)

        # Também tenta em qualquer coluna string/int se lista prioritária vazia
        if not nomes_prioritarios:
            nomes_prioritarios = [c.name for c in tabela.columns]

        for nome in nomes_prioritarios:
            coluna = tabela.c[nome]
            tipo_python = (
                coluna.type.python_type if hasattr(coluna.type, "python_type") else str
            )
            try:
                if tipo_python is int and (
                    termo.isdigit() or (termo.startswith("-") and termo[1:].isdigit())
                ):
                    candidatos.append(coluna == int(termo))
                elif tipo_python is str:
                    # igualdade exata e contém (case-insensitive via ilike)
                    candidatos.append(coluna == termo)
                    candidatos.append(coluna.ilike(f"%{termo}%"))
                elif tipo_python is not bool:
                    # tenta igualdade tipada
                    valor = _converter_valor_filtro(tabela, nome, termo)
                    candidatos.append(coluna == valor)
            except (ValueError, TypeError):
                continue

        if candidatos:
            condicoes.append(or_(*candidatos))

    if not condicoes:
        return None
    if len(condicoes) == 1:
        return condicoes[0]
    return and_(*condicoes)


async def contar_linhas(
    nome_da_tabela: str,
    *,
    filtros: dict[str, str] | None = None,
    busca_livre: str | None = None,
) -> int:
    """Total de linhas da tabela (com filtro opcional)."""
    tabela = obter_tabela(nome_da_tabela)
    consulta = select(func.count()).select_from(tabela)
    condicao = _montar_condicao_filtro(tabela, filtros, busca_livre)
    if condicao is not None:
        consulta = consulta.where(condicao)

    async with async_session() as sessao:
        resultado = await sessao.execute(consulta)
        return int(resultado.scalar_one() or 0)


async def listar_linhas(
    nome_da_tabela: str,
    *,
    pagina: int = 0,
    por_pagina: int = LINHAS_POR_PAGINA,
    filtros: dict[str, str] | None = None,
    busca_livre: str | None = None,
) -> list[dict[str, Any]]:
    """
    Lê uma página de linhas como lista de dicionários.

    Aceita filtros por coluna (igualdade) e busca livre (IDs / texto).
    """
    tabela = obter_tabela(nome_da_tabela)
    deslocamento = max(0, pagina) * por_pagina
    consulta = select(tabela)
    condicao = _montar_condicao_filtro(tabela, filtros, busca_livre)
    if condicao is not None:
        consulta = consulta.where(condicao)
    consulta = consulta.offset(deslocamento).limit(por_pagina)

    async with async_session() as sessao:
        resultado = await sessao.execute(consulta)
        linhas: list[dict[str, Any]] = []
        for linha in resultado.mappings().all():
            linhas.append(dict(linha))
        return linhas


async def buscar_linha_por_pk(
    nome_da_tabela: str,
    valores_pk: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Busca uma linha pelas colunas de chave primária.
    """
    tabela = obter_tabela(nome_da_tabela)
    consulta = select(tabela)
    for nome_coluna, valor in valores_pk.items():
        consulta = consulta.where(tabela.c[nome_coluna] == valor)
    async with async_session() as sessao:
        resultado = await sessao.execute(consulta)
        mapeamento = resultado.mappings().first()
        if mapeamento is None:
            return None
        return dict(mapeamento)


async def apagar_linha_por_pk(
    nome_da_tabela: str,
    valores_pk: dict[str, Any],
) -> bool:
    """
    Apaga a linha identificada pela PK. Devolve True se apagou.
    """
    tabela = obter_tabela(nome_da_tabela)
    comando = delete(tabela)
    for nome_coluna, valor in valores_pk.items():
        comando = comando.where(tabela.c[nome_coluna] == valor)
    async with async_session() as sessao:
        resultado = await sessao.execute(comando)
        await sessao.commit()
        return int(resultado.rowcount or 0) > 0


async def atualizar_campo(
    nome_da_tabela: str,
    valores_pk: dict[str, Any],
    nome_do_campo: str,
    valor_novo: Any,
) -> bool:
    """
    Atualiza um único campo da linha. Devolve True se alterou.
    """
    tabela = obter_tabela(nome_da_tabela)
    if nome_do_campo not in tabela.c:
        raise KeyError(f"Campo desconhecido: {nome_do_campo}")
    if nome_do_campo in listar_chaves_primarias(nome_da_tabela):
        raise ValueError("Não é permitido alterar a chave primária por este painel.")

    valor_convertido = _converter_valor(tabela, nome_do_campo, valor_novo)
    comando = tabela.update().values({nome_do_campo: valor_convertido})
    for nome_coluna, valor in valores_pk.items():
        comando = comando.where(tabela.c[nome_coluna] == valor)

    async with async_session() as sessao:
        resultado = await sessao.execute(comando)
        await sessao.commit()
        return int(resultado.rowcount or 0) > 0


async def inserir_linha(
    nome_da_tabela: str,
    valores: dict[str, Any],
) -> dict[str, Any]:
    """
    Insere uma linha com os campos informados.
    """
    tabela = obter_tabela(nome_da_tabela)
    valores_convertidos: dict[str, Any] = {}
    for nome_campo, valor in valores.items():
        if nome_campo not in tabela.c:
            continue
        if valor is None or valor == "":
            continue
        valores_convertidos[nome_campo] = _converter_valor(
            tabela,
            nome_campo,
            valor,
        )

    async with async_session() as sessao:
        resultado = await sessao.execute(
            tabela.insert().values(**valores_convertidos).returning(tabela)
        )
        await sessao.commit()
        linha = resultado.mappings().first()
        return dict(linha) if linha is not None else valores_convertidos


def _converter_valor(tabela: Table, nome_do_campo: str, valor_bruto: Any) -> Any:
    """
    Converte texto do modal para o tipo aproximado da coluna.
    """
    if valor_bruto is None:
        return None
    if isinstance(valor_bruto, str) and valor_bruto.strip().lower() in (
        "null",
        "none",
        "∅",
    ):
        return None

    coluna = tabela.c[nome_do_campo]
    tipo_python = (
        coluna.type.python_type if hasattr(coluna.type, "python_type") else str
    )

    if valor_bruto == "" and not coluna.nullable:
        raise ValueError(f"O campo `{nome_do_campo}` não pode ficar vazio.")

    if tipo_python is bool:
        texto = str(valor_bruto).strip().lower()
        if texto in ("1", "true", "sim", "yes", "s", "v"):
            return True
        if texto in ("0", "false", "nao", "não", "no", "n", "f"):
            return False
        raise ValueError(f"Valor booleano inválido para `{nome_do_campo}`.")

    if tipo_python is int:
        return int(str(valor_bruto).strip())

    if tipo_python is float:
        return float(str(valor_bruto).strip().replace(",", "."))

    return str(valor_bruto)


def resumo_da_linha(
    nome_da_tabela: str,
    linha: dict[str, Any],
) -> str:
    """
    Uma linha curta para o select (PK + um campo legível).
    """
    pks = listar_chaves_primarias(nome_da_tabela)
    partes_pk = [f"{chave}={_formatar_celula(linha.get(chave))}" for chave in pks]
    extra = ""
    # Prefere mostrar id_fivem / nickname quando existirem
    for preferida in ("id_fivem", "nickname_atual", "status", "cargo"):
        if (
            preferida in linha
            and preferida not in pks
            and linha.get(preferida) is not None
        ):
            extra = f"{preferida}={_formatar_celula(linha.get(preferida))}"
            break
    if not extra:
        for nome_coluna, valor in linha.items():
            if nome_coluna in pks:
                continue
            extra = _formatar_celula(valor)
            break
    base = " · ".join(partes_pk)
    if extra:
        return f"{base} · {extra}"[:100]
    return base[:100]


def codificar_pk(valores_pk: dict[str, Any]) -> str:
    """Empacota a PK num custom_id (chave=valor|chave=valor)."""
    partes = [f"{chave}={valores_pk[chave]}" for chave in sorted(valores_pk)]
    return "|".join(partes)


def decodificar_pk(texto: str) -> dict[str, Any]:
    """Desempacota a PK do custom_id / value do select."""
    resultado: dict[str, Any] = {}
    if not texto:
        return resultado
    for parte in texto.split("|"):
        if "=" not in parte:
            continue
        chave, valor = parte.split("=", 1)
        # tenta int quando for número puro
        if valor.isdigit() or (valor.startswith("-") and valor[1:].isdigit()):
            resultado[chave] = int(valor)
        elif valor in ("True", "False"):
            resultado[chave] = valor == "True"
        elif valor == "None":
            resultado[chave] = None
        else:
            resultado[chave] = valor
    return resultado


def pk_da_linha(nome_da_tabela: str, linha: dict[str, Any]) -> dict[str, Any]:
    """Extrai só as colunas de PK da linha."""
    return {
        chave: linha.get(chave) for chave in listar_chaves_primarias(nome_da_tabela)
    }


def texto_do_filtro_ativo(
    filtros: dict[str, str] | None,
    busca_livre: str | None,
) -> str | None:
    """Resumo legível do filtro atual para o cabeçalho do painel."""
    partes: list[str] = []
    if busca_livre and busca_livre.strip():
        partes.append(f"busca=`{busca_livre.strip()}`")
    if filtros:
        for coluna, valor in filtros.items():
            if valor is None or str(valor).strip() == "":
                continue
            partes.append(f"{coluna}=`{valor}`")
    if not partes:
        return None
    return " · ".join(partes)
