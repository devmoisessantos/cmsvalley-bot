"""
Acesso ao banco para o painel administrativo.

Só opera sobre tabelas registradas em ``models.Base.metadata``.
Nunca monta SQL com texto livre do membro.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Table,
    delete,
    select,
)

from src.database.conexao import (
    async_session,
    engine,
)
from src.database.models import Base

# Quantas linhas por página no painel
LINHAS_POR_PAGINA = 10


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


def _formatar_celula(valor: Any) -> str:
    """Texto curto para caber no select / card."""
    if valor is None:
        return "∅"
    texto = str(valor)
    if len(texto) > 40:
        return texto[:37] + "..."
    return texto


async def contar_linhas(nome_da_tabela: str) -> int:
    """Total de linhas da tabela."""
    from sqlalchemy import func

    tabela = obter_tabela(nome_da_tabela)
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(func.count()).select_from(tabela)
        )
        return int(resultado.scalar_one() or 0)


async def listar_linhas(
    nome_da_tabela: str,
    *,
    pagina: int = 0,
    por_pagina: int = LINHAS_POR_PAGINA,
) -> list[dict[str, Any]]:
    """
    Lê uma página de linhas como lista de dicionários.
    """
    tabela = obter_tabela(nome_da_tabela)
    deslocamento = max(0, pagina) * por_pagina
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(tabela).offset(deslocamento).limit(por_pagina)
        )
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
    tipo_python = coluna.type.python_type if hasattr(coluna.type, "python_type") else str

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
        chave: linha.get(chave)
        for chave in listar_chaves_primarias(nome_da_tabela)
    }
