# src/services/recrutamento_service.py
from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import (
    delete,
    select,
)

from src.config import (
    CANAIS,
    CARGOS,
)
from src.database.connection import async_session
from src.database.models import (
    EstadoPlantao,
    Recrutamento,
    RespostaProva,
    Usuario,
)
from src.recrutamento.recrutamento_logs import NovoRecrutamentoLog
from src.utils.logger import log_mudanca_cargo

# Status que ainda contam como processo em andamento (travam novo início se não cancelar)
STATUS_RECRUTAMENTO_ATIVOS = (
    "ESTUDANDO",
    "PROVA_LIBERADA",
    "EM_PROVA",
)

# Cargos de fluxo de recrutamento que NÃO devem voltar no rejoin
# se o processo falhou / foi cancelado (máx.: Visitantes + ESTUDANTE)
NOMES_CARGOS_RECRUTAMENTO_SENSIVEIS = (
    "PROVA",
    "Aprovado",
)


async def buscar_recrutamento_ativo(discord_id_candidato: int) -> Recrutamento | None:
    """Retorna o recrutamento ativo mais recente do candidato, se houver."""
    async with async_session() as session:
        resultado = await session.execute(
            select(Recrutamento)
            .where(
                Recrutamento.discord_id_candidato == discord_id_candidato,
                Recrutamento.status.in_(STATUS_RECRUTAMENTO_ATIVOS),
            )
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


async def candidato_tem_fluxo_incompleto(discord_id: int) -> bool:
    """
    True se o último recrutamento relevante ainda está ativo ou foi
    CANCELADO (saída do servidor / falha) sem aprovação.
    Usado no rejoin para limitar cargos restaurados.
    """
    async with async_session() as session:
        resultado = await session.execute(
            select(Recrutamento)
            .where(Recrutamento.discord_id_candidato == discord_id)
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        ultimo = resultado.scalar_one_or_none()
        if ultimo is None:
            return False
        if ultimo.status in STATUS_RECRUTAMENTO_ATIVOS:
            return True
        if ultimo.status == "CANCELADO":
            return True
        return False


async def cancelar_recrutamento_ativo(
    discord_id_candidato: int,
    *,
    motivo: str = "Cancelado manualmente",
    guild: discord.Guild | None = None,
    executor: discord.abc.User | None = None,
    apagar_respostas: bool = True,
) -> Recrutamento | None:
    """
    Marca recrutamento ativo como CANCELADO, limpa formulário e
    remove cargos de PROVA/ESTUDANTE (mantém Visitantes).

    Retorna o registro cancelado ou None se não havia ativo.
    """
    async with async_session() as session:
        resultado = await session.execute(
            select(Recrutamento)
            .where(
                Recrutamento.discord_id_candidato == discord_id_candidato,
                Recrutamento.status.in_(STATUS_RECRUTAMENTO_ATIVOS),
            )
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        recrutamento = resultado.scalar_one_or_none()
        if recrutamento is None:
            return None

        recrutamento.status = "CANCELADO"
        recrutamento.data_fim = datetime.now(timezone.utc)
        recrutamento.formulario_aberto = False
        # Guarda motivo curto no campo cargo_final só se estiver vazio
        # (evita coluna nova; texto curto de auditoria)
        if not recrutamento.cargo_final:
            recrutamento.cargo_final = f"CANCEL:{motivo[:24]}"

        resultado_usuario = await session.execute(
            select(Usuario).where(Usuario.discord_id == discord_id_candidato)
        )
        usuario = resultado_usuario.scalar_one_or_none()
        if usuario is not None and not usuario.ja_foi_aprovado:
            usuario.status = "VISITANTE"

        if apagar_respostas:
            await session.execute(
                delete(RespostaProva).where(
                    RespostaProva.recrutamento_id == recrutamento.id
                )
            )

        await session.commit()
        # Detach values we need after session closes
        recrutamento_id = recrutamento.id
        status_final = recrutamento.status

    # Ajuste de cargos no Discord (fora da sessão)
    if guild is not None:
        membro = guild.get_member(discord_id_candidato)
        if membro is not None:
            cargos_remover = []
            for nome_cargo in ("PROVA", "ESTUDANTE"):
                cargo = guild.get_role(CARGOS.get(nome_cargo, 0))
                if cargo is not None and cargo in membro.roles:
                    cargos_remover.append(cargo)
            if cargos_remover:
                try:
                    await membro.remove_roles(
                        *cargos_remover,
                        reason=f"Recrutamento cancelado: {motivo}"[:500],
                    )
                    if executor is not None:
                        try:
                            await log_mudanca_cargo(
                                guild,
                                candidato=membro,
                                executor=executor,
                                cargos_removidos=[c.mention for c in cargos_remover],
                            )
                        except Exception:
                            pass
                except (discord.Forbidden, discord.HTTPException):
                    pass

            # Garante Visitantes se ainda for fluxo de entrada
            cargo_visitante = guild.get_role(CARGOS.get("Visitantes", 0))
            if cargo_visitante is not None and cargo_visitante not in membro.roles:
                try:
                    await membro.add_roles(
                        cargo_visitante,
                        reason="Recrutamento cancelado — mantém Visitantes",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

    # Re-fetch for return
    async with async_session() as session:
        resultado = await session.execute(
            select(Recrutamento).where(Recrutamento.id == recrutamento_id)
        )
        return resultado.scalar_one_or_none()


async def cancelar_por_saida_do_servidor(discord_id: int) -> bool:
    """
    Chamado no on_member_remove.
    Se havia recrutamento ativo, cancela para não travar reentrada / novo recrutamento.
    """
    cancelado = await cancelar_recrutamento_ativo(
        discord_id,
        motivo="Saiu do servidor",
        guild=None,
        executor=None,
        apagar_respostas=True,
    )
    return cancelado is not None


async def validar_e_iniciar_recrutamento(
    interaction: discord.Interaction,
    candidato: discord.Member,
    recrutador: discord.Member,
    id_fivem: str,
):
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    cargo_visitante = guild.get_role(CARGOS["Visitantes"])
    cargo_estudante = guild.get_role(CARGOS["ESTUDANTE"])
    cargo_hp = guild.get_role(CARGOS["HP S・Valley"])
    cargo_aprovado = guild.get_role(CARGOS["Aprovado"])

    # Validações do fluxo
    if cargo_visitante not in candidato.roles:
        await interaction.followup.send(
            "❌ Este membro não possui o cargo Visitantes (não concluiu a WhiteList).",
            ephemeral=True,
        )
        return

    if cargo_hp in candidato.roles or cargo_aprovado in candidato.roles:
        await interaction.followup.send(
            "❌ Este membro já foi aprovado anteriormente.", ephemeral=True
        )
        return

    async with async_session() as session:
        resultado = await session.execute(
            select(Usuario).where(Usuario.discord_id == candidato.id)
        )
        usuario = resultado.scalar_one_or_none()

        if usuario is None:
            usuario = Usuario(
                discord_id=candidato.id, nickname_atual=candidato.display_name
            )
            session.add(usuario)

        # Checa cooldown de 24h após reprovação
        if usuario.data_ultima_reprovacao:
            # antes: datetime.now(timezone.utc)
            tempo_passado = datetime.now(timezone.utc) - usuario.data_ultima_reprovacao
            if tempo_passado.total_seconds() < 24 * 3600:
                horas_restantes = 24 - (tempo_passado.total_seconds() / 3600)
                await interaction.followup.send(
                    f"❌ Este candidato precisa aguardar mais {horas_restantes:.1f}h para tentar novamente.",
                    ephemeral=True,
                )
                return

        # Se já existe processo ativo (ESTUDANDO / PROVA_LIBERADA / EM_PROVA),
        # cancela o antigo para o recrutador poder reiniciar (falha de interação,
        # bot reiniciado, formulário preso, etc.). Não bloqueia mais o fluxo.
        resultado_recrutamento = await session.execute(
            select(Recrutamento).where(
                Recrutamento.discord_id_candidato == candidato.id,
                Recrutamento.status.in_(list(STATUS_RECRUTAMENTO_ATIVOS)),
            )
        )
        recrutamentos_ativos = list(resultado_recrutamento.scalars().all())
        for antigo in recrutamentos_ativos:
            antigo.status = "CANCELADO"
            antigo.data_fim = datetime.now(timezone.utc)
            antigo.formulario_aberto = False
            if not antigo.cargo_final:
                antigo.cargo_final = "CANCEL:reinicio"
            await session.execute(
                delete(RespostaProva).where(RespostaProva.recrutamento_id == antigo.id)
            )

        # dentro de validar_e_iniciar_recrutamento, antes de criar o novo_recrutamento:
        resultado_duplicidade = await session.execute(
            select(Recrutamento).where(
                Recrutamento.id_fivem == id_fivem,
                Recrutamento.discord_id_candidato != candidato.id,
                Recrutamento.status.in_(
                    ["ESTUDANDO", "EM_PROVA", "PROVA_LIBERADA", "APROVADO"]
                ),
            )
        )
        conflito = resultado_duplicidade.scalar_one_or_none()

        if conflito is not None:
            await interaction.followup.send(
                f"⚠️ O ID FiveM `{id_fivem}` já está associado a <@{conflito.discord_id_candidato}>. "
                f"Confira se digitou o ID correto antes de continuar.",
                ephemeral=True,
            )
            return

        # Tudo certo: cria o recrutamento
        novo_recrutamento = Recrutamento(
            discord_id_candidato=candidato.id,
            discord_id_recrutador=recrutador.id,
            id_fivem=id_fivem,
            status="ESTUDANDO",
        )
        session.add(novo_recrutamento)
        usuario.status = "ESTUDANTE"
        await session.commit()

    # Remove PROVA residual de processo anterior (se houver) e aplica ESTUDANTE
    cargo_prova = guild.get_role(CARGOS.get("PROVA", 0))
    cargos_limpar = []
    if cargo_prova is not None and cargo_prova in candidato.roles:
        cargos_limpar.append(cargo_prova)
    if cargos_limpar:
        try:
            await candidato.remove_roles(
                *cargos_limpar,
                reason="Limpeza de cargo de prova de recrutamento anterior",
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    await candidato.add_roles(
        cargo_estudante,
        reason=f"Recrutamento iniciado por {recrutador}",
    )

    # 👇 Responde AO USUÁRIO primeiro — isso garante que ele vê o sucesso mesmo se o log falhar depois
    await interaction.followup.send(
        f"✅ Recrutamento iniciado para {candidato.mention}. Cargo Estudante aplicado.",
        ephemeral=True,
    )

    # 👇 Logs vêm DEPOIS, protegidos por try/except — falha aqui não derruba a experiência do recrutador
    try:
        await log_mudanca_cargo(
            guild,
            candidato=candidato,
            executor=recrutador,
            cargos_adicionados=[cargo_estudante.mention],
        )

        canal_log_inicio = guild.get_channel(CANAIS["LOG_RECRUTAMENTOS"])
        if canal_log_inicio:
            await canal_log_inicio.send(
                view=NovoRecrutamentoLog(
                    candidato=candidato,
                    recrutador=recrutador,
                    cargo_role=cargo_estudante,
                    id_fivem=id_fivem,
                    guild=guild,
                )
            )
    except Exception as erro:
        canal_erros = guild.get_channel(CANAIS["LOG_ERROS"])
        if canal_erros:
            await canal_erros.send(
                f"⚠️ Falha ao registrar log de início de recrutamento: `{erro}`"
            )


async def resolver_id_fivem(discord_id: int) -> str | None:
    """
    Prioridade:
      1) usuarios.id_fivem
      2) EstadoPlantao.id_fivem
      3) Recrutamento com passaporte (qualquer status; preferindo o mais recente)
    """
    async with async_session() as session:
        from src.database.models import Usuario

        resultado_usuario = await session.execute(
            select(Usuario.id_fivem).where(
                Usuario.discord_id == discord_id,
                Usuario.id_fivem.is_not(None),
            )
        )
        id_usuario = resultado_usuario.scalar_one_or_none()
        if id_usuario:
            return str(id_usuario)

        resultado = await session.execute(
            select(EstadoPlantao.id_fivem).where(
                EstadoPlantao.discord_id == discord_id,
                EstadoPlantao.id_fivem.is_not(None),
            )
        )
        id_fivem_salvo = resultado.scalar_one_or_none()
        if id_fivem_salvo:
            return str(id_fivem_salvo)

        resultado_rec = await session.execute(
            select(Recrutamento.id_fivem)
            .where(
                Recrutamento.discord_id_candidato == discord_id,
                Recrutamento.id_fivem.is_not(None),
            )
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        valor = resultado_rec.scalar_one_or_none()
        return str(valor) if valor else None
