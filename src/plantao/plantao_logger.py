"""
Registro visual dos eventos de plantao no canal de logs.

O dicionario EVENTOS_PLANTAO no topo diz, para cada tipo de evento, como ele
deve aparecer: titulo, emoji e cor. Concentrar isso num lugar so evita que cada
arquivo invente seu proprio jeito de escrever "entrou em servico".

`resolver_id_fivem_e_validar` existe porque o log precisa mostrar o ID FiveM da
pessoa, e esse ID vem da ficha de recrutamento. Quando nao ha ficha, o log sai
sem o ID em vez de falhar.
"""

import discord
from sqlalchemy import select

from src.config import CANAIS
from src.database.conexao import async_session
from src.database.models import (
    LogPlantao,
    Recrutamento,
)
from src.utils.formatacao import formatar_hms

EVENTOS_PLANTAO = {
    "TOGGLE_ON": (" ✅ Entrou em Serviço", discord.Color.green()),
    "TOGGLE_OFF": (" ❌ Saiu de Serviço", discord.Color.red()),
    "ENTROU_CALL": (" 📞 Entrou no Canal de Voz", discord.Color.blurple()),
    "SAIU_CALL": (" 📴 Saiu do Canal de Voz", discord.Color.dark_grey()),
    "CALL_ENCERRADA": (
        " ⏹️ Saiu de Serviço (permaneceu na call)",
        discord.Color.orange(),
    ),
    "TROCOU_CALL": (" 🔄 Mudou de Canal de Voz", discord.Color.gold()),
    "MOEDA_CREDITADA": (" 💰 Crédito de Moeda Adicionado", discord.Color.green()),
    "LEMBRETE_10": (" 📌 Lembrete: 10 minutos inativo", discord.Color.orange()),
    "LEMBRETE_15": (" ⚠️ Atenção: 15 minutos inativo", discord.Color.orange()),
    "LEMBRETE_25": (" 🚨 Último aviso: 25 minutos inativo", discord.Color.red()),
    "DESLIGAMENTO_AUTOMATICO": (" ⏹️ Encerramento Automático", discord.Color.red()),
    "HOUSEKEEPING": (" 🧹 Limpeza de Plantão Ativo", discord.Color.dark_grey()),
    "OCIOSO_ENCERRADO": (" ▶️ Plantão Reativado", discord.Color.blurple()),
    "AFK_AVISO": ("🔇 Aviso de AFK (mudo+surdo)", discord.Color.orange()),
    "CALL_ENCERRADA_POR_AFK": (
        "⏹️ Call Encerrada (AFK detectado)",
        discord.Color.dark_orange(),
    ),
    "PENALIDADE_AFK": ("⚠️ Penalidade Aplicada (AFK)", discord.Color.dark_red()),
    "TROCA_MOEDAS_SOLICITADA": (
        "💵 Troca de Moedas Solicitada",
        discord.Color.gold(),
    ),
}


async def obter_id_fivem_de_recrutamento(discord_id: int) -> str | None:
    """Busca o ID FiveM do recrutamento aprovado mais recente. Chamada só na origem
    (ao ligar o serviço) — os eventos subsequentes usam o valor já congelado em EstadoPlantao."""
    async with async_session() as session:
        resultado = await session.execute(
            select(Recrutamento)
            .where(
                Recrutamento.discord_id_candidato == discord_id,
                Recrutamento.status == "APROVADO",
            )
            .order_by(Recrutamento.data_fim.desc())
        )
        recrutamento = resultado.scalars().first()
        return recrutamento.id_fivem if recrutamento else None


async def registrar_evento_plantao(
    guild: discord.Guild,
    discord_id: int,
    evento: str,
    id_fivem: str | None,  # 👈 agora é passado, não buscado
    *,
    canal_id: int | None = None,
    duracao_segundos: int | None = None,
    detalhes: str | None = None,
    campos_extra: dict[str, str] | None = None,
):
    """Persiste um evento de plantão e tenta publicá-lo no canal de auditoria.

    O registro no banco acontece antes do envio ao Discord para não perder a evidência
    quando o canal está ausente ou indisponível. Campos extras permitem acrescentar
    contexto sem forçar cada evento a ter um formato novo no modelo de dados.
    """

    async with async_session() as session:
        session.add(
            LogPlantao(
                id_fivem=id_fivem,
                discord_id=discord_id,
                evento=evento,
                canal_id=canal_id,
                duracao_segundos=duracao_segundos,
                detalhes=detalhes,
            )
        )
        await session.commit()

    canal = guild.get_channel(CANAIS["LOG_PLANTAO"])
    if canal is None:
        # canal ainda não configurado — o registro no banco já aconteceu, só não posta
        return

    titulo, cor = EVENTOS_PLANTAO.get(evento, (evento, discord.Color.blurple()))
    membro = guild.get_member(discord_id)
    mencao = membro.mention if membro else f"`{discord_id}`"

    linhas = f"- **Membro:** {mencao}\n- **ID FiveM:** `{id_fivem or 'N/A'}`"

    if canal_id:
        canal_ref = guild.get_channel(canal_id)
        nome_canal = canal_ref.name if canal_ref else f"`{canal_id}`"
        linhas += f"\n- **Call:** {nome_canal}"

    if duracao_segundos is not None:
        linhas += f"\n- **Duração:** {formatar_hms(duracao_segundos)}"

    if campos_extra:  # 👈 novo
        for chave, valor in campos_extra.items():
            linhas += f"\n- **{chave}:** {valor}"

    if detalhes:
        linhas += f"\n- **Detalhes:** {detalhes}"

    from src.utils.log_container import LogContainerView

    view = LogContainerView(
        titulo=titulo,
        linhas=linhas,
        guild=guild,
        cor=cor,
        avatar_url=membro.display_avatar.url if membro else None,
    )
    await canal.send(view=view)

    # Espelha eventos de horas no canal LOG_HORAS (quando configurado)
    eventos_de_horas = {
        "TOGGLE_ON",
        "TOGGLE_OFF",
        "SAIU_CALL",
        "CALL_ENCERRADA",
        "DESLIGAMENTO_AUTOMATICO",
        "CALL_ENCERRADA_POR_AFK",
    }
    if evento in eventos_de_horas:
        from src.utils.logger import publicar_log_auditoria

        await publicar_log_auditoria(
            guild,
            "LOG_HORAS",
            titulo=titulo,
            linhas=linhas,
            cor=cor,
            url_do_avatar=membro.display_avatar.url if membro else None,
        )


async def resolver_id_fivem_e_validar(discord_id: int) -> str | None:
    """Retorna o id_fivem se o discord_id tiver um Recrutamento aprovado.
    Única fonte de verdade: quem o recrutador aprovou de fato."""
    async with async_session() as session:
        resultado = await session.execute(
            select(Recrutamento.id_fivem)
            .where(
                Recrutamento.discord_id_candidato == discord_id,
                Recrutamento.status == "APROVADO",
                Recrutamento.id_fivem.is_not(None),
            )
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()
