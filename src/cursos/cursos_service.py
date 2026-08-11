"""Lógica de catálogo, posse, pacote e ciclo de vida do pedido de curso."""

from __future__ import annotations

import json
import logging
import math

import discord
from sqlalchemy import select

from src.config import (
    CURSOS,
    VALOR_MOEDA_INGAME,
)
from src.database.connection import async_session
from src.database.models import (
    EstadoPlantao,
    SolicitacaoCurso,
    agora,
)
from src.utils.formatacao import formatar_reais

logger = logging.getLogger(__name__)


def listar_cursos_ordenados() -> list[tuple[str, dict]]:
    """Lista (chave, dados) — práticos 1.0, 2.0, depois função/diretoria."""
    ordem_nivel = {"1.0": 0, "2.0": 1, "funcao": 2, "diretoria": 3}
    itens = list(CURSOS.items())
    itens.sort(
        key=lambda par: (ordem_nivel.get(par[1].get("nivel"), 9), par[1]["nome"])
    )
    return itens


def obter_curso(chave: str) -> dict | None:
    return CURSOS.get(chave)


def moedas_necessarias_para_valor(valor_ingame: int) -> int:
    if valor_ingame <= 0:
        return 0
    return max(1, math.ceil(valor_ingame / VALOR_MOEDA_INGAME))


def moedas_necessarias_para_curso(chave: str) -> int:
    dados = obter_curso(chave)
    if not dados:
        return 0
    return moedas_necessarias_para_valor(int(dados.get("valor_ingame") or 0))


def moedas_necessarias_para_pacote(chaves: list[str]) -> int:
    total_valor = soma_valor_ingame(chaves)
    return moedas_necessarias_para_valor(total_valor)


def soma_valor_ingame(chaves: list[str]) -> int:
    total = 0
    for chave in chaves:
        dados = obter_curso(chave)
        if dados:
            total += int(dados.get("valor_ingame") or 0)
    return total


def membro_tem_curso(membro: discord.Member, chave: str) -> bool:
    dados = obter_curso(chave)
    if not dados:
        return False
    cargo_id = int(dados["cargo_id"])
    return any(cargo.id == cargo_id for cargo in membro.roles)


def listar_cursos_que_faltam(
    membro: discord.Member,
    chaves: list[str],
) -> list[str]:
    return [chave for chave in chaves if not membro_tem_curso(membro, chave)]


def rotulo_curso(chave: str) -> str:
    dados = obter_curso(chave)
    if not dados:
        return chave
    return f"{dados.get('emoji', '')} {dados['nome']}".strip()


def menção_cargo_curso(chave: str) -> str:
    dados = obter_curso(chave)
    if not dados:
        return f"`{chave}`"
    return f"<@&{int(dados['cargo_id'])}>"


def parse_chaves_json(texto: str | None, chave_unica: str | None = None) -> list[str]:
    if texto:
        try:
            lista = json.loads(texto)
            if isinstance(lista, list):
                return [str(item) for item in lista]
        except json.JSONDecodeError:
            pass
    if chave_unica and chave_unica != "pacote":
        return [chave_unica]
    return []


async def consultar_saldo_moedas(discord_id: int) -> int:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            return 0
        return int(estado.saldo_moedas or 0)


async def debitar_moedas_curso(
    discord_id: int,
    quantidade: int,
) -> tuple[bool, int, str]:
    """Debita moedas. Retorna (ok, saldo_restante, mensagem_erro)."""
    if quantidade <= 0:
        return True, await consultar_saldo_moedas(discord_id), ""

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            return False, 0, "Você ainda não tem saldo de plantão registrado."
        saldo = int(estado.saldo_moedas or 0)
        if saldo < quantidade:
            return (
                False,
                saldo,
                (
                    f"Saldo insuficiente. Você tem **{saldo}** moeda(s) "
                    f"e precisa de **{quantidade}**."
                ),
            )
        estado.saldo_moedas = saldo - quantidade
        await sessao.commit()
        return True, int(estado.saldo_moedas), ""


async def creditar_moedas_instrutor(
    discord_id: int,
    quantidade: int,
) -> int:
    """Credita moedas ao instrutor. Retorna saldo final."""
    if quantidade <= 0:
        return await consultar_saldo_moedas(discord_id)

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
        )
        estado = resultado.scalar_one_or_none()
        if estado is None:
            estado = EstadoPlantao(discord_id=discord_id, saldo_moedas=0)
            sessao.add(estado)
        estado.saldo_moedas = int(estado.saldo_moedas or 0) + quantidade
        await sessao.commit()
        return int(estado.saldo_moedas)


async def registrar_solicitacao_pacote(
    *,
    discord_id: int,
    chaves: list[str],
    forma_pagamento: str,
    moedas_debitadas: int,
    observacao_aluno: str | None,
) -> SolicitacaoCurso:
    valor_total = soma_valor_ingame(chaves)
    chave_resumo = chaves[0] if len(chaves) == 1 else "pacote"
    async with async_session() as sessao:
        registro = SolicitacaoCurso(
            discord_id=discord_id,
            chave_curso=chave_resumo,
            chaves_cursos_json=json.dumps(chaves, ensure_ascii=False),
            valor_ingame=valor_total,
            moedas_debitadas=moedas_debitadas,
            forma_pagamento=forma_pagamento,
            status="AGENDADO",
            observacao_aluno=(observacao_aluno or "").strip() or None,
            criado_em=agora(),
            atualizado_em=agora(),
        )
        sessao.add(registro)
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def obter_solicitacao_curso(solicitacao_id: int) -> SolicitacaoCurso | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        return resultado.scalar_one_or_none()


async def buscar_pedido_aberto(discord_id: int) -> SolicitacaoCurso | None:
    """Pedido ainda em andamento (agendado ou aceito) do aluno."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso)
            .where(SolicitacaoCurso.discord_id == discord_id)
            .where(SolicitacaoCurso.status.in_(("AGENDADO", "ACEITO")))
            .order_by(SolicitacaoCurso.id.asc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


async def mesclar_cursos_no_pedido(
    *,
    solicitacao_id: int,
    novas_chaves: list[str],
    forma_pagamento: str,
    moedas_extra: int,
    observacao_aluno: str | None,
) -> SolicitacaoCurso | None:
    """
    Acrescenta cursos a um pedido aberto.
    Só adiciona chaves que ainda não estavam no pacote.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return None
        if registro.status not in ("AGENDADO", "ACEITO"):
            return registro

        atuais = parse_chaves_json(registro.chaves_cursos_json, registro.chave_curso)
        unidas: list[str] = []
        for chave in atuais + list(novas_chaves):
            if chave not in unidas:
                unidas.append(chave)

        registro.chaves_cursos_json = json.dumps(unidas, ensure_ascii=False)
        registro.chave_curso = unidas[0] if len(unidas) == 1 else "pacote"
        registro.valor_ingame = soma_valor_ingame(unidas)
        registro.moedas_debitadas = int(registro.moedas_debitadas or 0) + int(
            moedas_extra
        )
        # Mantém forma já paga em moedas se já havia débito
        if forma_pagamento == "MOEDAS" or int(registro.moedas_debitadas or 0) > 0:
            if int(registro.moedas_debitadas or 0) > 0:
                registro.forma_pagamento = "MOEDAS"
        elif forma_pagamento:
            registro.forma_pagamento = forma_pagamento

        if observacao_aluno and observacao_aluno.strip():
            anterior = (registro.observacao_aluno or "").strip()
            nova = observacao_aluno.strip()
            if anterior and nova not in anterior:
                registro.observacao_aluno = f"{anterior}\n---\n{nova}"
            elif not anterior:
                registro.observacao_aluno = nova

        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def decidir_cursos_parciais(
    *,
    solicitacao_id: int,
    chaves_aprovadas: list[str],
    chaves_reprovadas: list[str],
    instrutor_id: int,
) -> SolicitacaoCurso | None:
    """
    Fecha o pedido com listas de aprovados/reprovados.
    Status final: APROVADO se houver ao menos um aprovado; senão REPROVADO.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return None
        if registro.status not in ("ACEITO", "AGENDADO"):
            return registro

        # Guarda decisão no campo de observação do instrutor (resumo)
        texto_aprovados = ", ".join(chaves_aprovadas) or "—"
        texto_reprovados = ", ".join(chaves_reprovadas) or "—"
        resumo = f"Aprovados: {texto_aprovados}\nReprovados: {texto_reprovados}"
        anterior = (registro.observacao_instrutor or "").strip()
        if anterior:
            registro.observacao_instrutor = f"{anterior}\n{resumo}"
        else:
            registro.observacao_instrutor = resumo
        registro.aplicado_por = instrutor_id
        registro.status = "APROVADO" if chaves_aprovadas else "REPROVADO"
        # Se parcial, marca como APROVADO_PARCIAL no texto — status APROVADO se algum ok
        if chaves_aprovadas and chaves_reprovadas:
            registro.status = "APROVADO"
        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def marcar_mensagem_solicitacao_curso(
    solicitacao_id: int,
    canal_id: int,
    mensagem_id: int,
) -> None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return
        registro.mensagem_canal_id = canal_id
        registro.mensagem_id = mensagem_id
        registro.atualizado_em = agora()
        await sessao.commit()


async def aceitar_agendamento(
    *,
    solicitacao_id: int,
    instrutor_id: int,
    observacao_instrutor: str | None,
) -> SolicitacaoCurso | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None or registro.status != "AGENDADO":
            return registro
        registro.status = "ACEITO"
        registro.instrutor_id = instrutor_id
        registro.observacao_instrutor = (observacao_instrutor or "").strip() or None
        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def decidir_curso(
    *,
    solicitacao_id: int,
    aprovado: bool,
    instrutor_id: int,
    motivo: str | None = None,
) -> SolicitacaoCurso | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoCurso).where(SolicitacaoCurso.id == solicitacao_id)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return None
        if registro.status not in ("ACEITO", "AGENDADO"):
            return registro
        registro.status = "APROVADO" if aprovado else "REPROVADO"
        registro.aplicado_por = instrutor_id
        if not aprovado and motivo:
            registro.observacao_instrutor = (motivo or "")[:500]
        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def conceder_cargos_dos_cursos(
    membro: discord.Member,
    chaves: list[str],
) -> tuple[bool, str]:
    guilda = membro.guild
    cargos_para_adicionar: list[discord.Role] = []
    for chave in chaves:
        dados = obter_curso(chave)
        if not dados:
            continue
        cargo = guilda.get_role(int(dados["cargo_id"]))
        if cargo is None:
            return False, f"Cargo do curso `{chave}` não encontrado na guilda."
        if cargo not in membro.roles:
            cargos_para_adicionar.append(cargo)
    if not cargos_para_adicionar:
        return True, "Aluno já possuía os cargos dos cursos."
    try:
        await membro.add_roles(
            *cargos_para_adicionar,
            reason="Curso aprovado pelo instrutor",
        )
    except discord.Forbidden:
        return False, "Sem permissão para conceder cargos de curso."
    except discord.HTTPException as erro:
        return False, f"Erro Discord ao conceder cargos: {erro}"
    return True, "Cargos de curso concedidos."


def montar_linhas_corpo_pedido(
    *,
    membro: discord.Member,
    registro: SolicitacaoCurso,
    saldo_restante: int | None = None,
) -> tuple[str, str]:
    """
    Retorna (titulo, corpo_markdown) para o card de agendamento.
    """
    chaves = parse_chaves_json(registro.chaves_cursos_json, registro.chave_curso)
    forma = registro.forma_pagamento
    observacao = registro.observacao_aluno

    if len(chaves) <= 1:
        chave = chaves[0] if chaves else registro.chave_curso
        dados = obter_curso(chave) or {}
        emoji = dados.get("emoji") or "📚"
        titulo = f"{emoji} {dados.get('nome', chave)} — Pedido de Curso"
        corpo_cursos = (
            f"**Curso:** {menção_cargo_curso(chave)}\n"
            f"**Valor in-game:** `{formatar_reais(int(dados.get('valor_ingame') or 0))}`"
        )
    else:
        titulo = "📚 Pacote de Cursos — Pedido de Curso"
        linhas_itens = []
        for chave in chaves:
            dados = obter_curso(chave) or {}
            valor = formatar_reais(int(dados.get("valor_ingame") or 0))
            linhas_itens.append(f"> • {menção_cargo_curso(chave)} — `{valor}`")
        corpo_cursos = (
            "## 🛒 Cursos Solicitados\n"
            + "\n".join(linhas_itens)
            + f"\n\n**Total:** `{formatar_reais(int(registro.valor_ingame or 0))}`"
        )

    if forma == "MOEDAS":
        nota_instrutor = (
            "> 🔧 **Instrutor:** aplique o curso e as moedas serão "
            "creditadas após a finalização."
        )
    elif forma == "IN_GAME":
        nota_instrutor = (
            "> 🔧 **Instrutor:** confirme o pagamento **in-game** com o aluno "
            "antes de aprovar. As moedas de plantão **não** foram debitadas."
        )
    else:
        nota_instrutor = (
            "> 🔧 **Instrutor:** solicitação sem cobrança automática de moedas."
        )

    bloco_obs = ""
    if observacao:
        bloco_obs = f"\n### 📝 Observação do aluno\n> {observacao}\n"

    corpo = (
        f"**👤 Aluno:** {membro.mention} (`{membro.id}`)\n"
        f"**📋 Pedido:** `#{registro.id}`\n"
        f"{bloco_obs}\n"
        f"### 💰 Pagamento\n"
        f"{corpo_cursos}\n"
        f"**Forma de pagamento:** `{forma}`\n"
    )
    if registro.moedas_debitadas:
        corpo += f"**Moedas debitadas:** `{registro.moedas_debitadas}`\n"
    # Saldo restante NÃO aparece no canal — só o aluno vê no card efêmero
    corpo += f"\n{nota_instrutor}"

    return titulo, corpo
