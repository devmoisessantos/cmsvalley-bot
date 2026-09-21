# src/demissao/demissao_service.py
"""Regras de demissão voluntária e demissão por abandono (saída informal)."""

from __future__ import annotations

import json
import logging
from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import (
    delete,
    func,
    select,
)
from sqlalchemy.exc import SQLAlchemyError

from src.config import (
    CARGOS,
    CARGOS_DIRETORIA,
    CARGOS_HIERARQUIA,
    CARGOS_PUNICOES,
)
from src.database.conexao import async_session
from src.database.models import (
    EstadoPlantao,
    LogPlantao,
    Punicao,
    SnapshotCargosMembro,
    SolicitacaoDemissao,
    Usuario,
    agora,
)
from src.utils.error_handling import ignorar_falha_cosmetica
from src.utils.nickname import remover_prefixo_existente

registrador = logging.getLogger(__name__)


def cargo_atual_hierarquia(membro: discord.Member) -> str:
    """Maior cargo da hierarquia que o membro possui (ou '—' se nenhum)."""
    nomes = {cargo.name for cargo in membro.roles}
    for nome in CARGOS_HIERARQUIA:
        if nome in nomes:
            return nome
    return "—"


def membro_pode_solicitar_demissao(membro: discord.Member) -> bool:
    """Só quem tem cargo da hierarquia (não é só Visitante)."""
    nomes = {cargo.name for cargo in membro.roles}
    return bool(nomes.intersection(set(CARGOS_HIERARQUIA)))


def membro_e_diretoria(membro: discord.Member) -> bool:
    """Confere cargos de diretoria para proteger decisões de desligamento."""
    nomes = {cargo.name for cargo in membro.roles}
    return bool(nomes.intersection(set(CARGOS_DIRETORIA)))


async def contar_advertencias_ativas(discord_id: int) -> int:
    """Conta punições ainda ativas para registrar o contexto do pedido.

    Executa a agregação no banco em vez de carregar punições individuais, pois
    o total é apenas um dado histórico apresentado na análise da diretoria.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(func.count())
            .select_from(Punicao)
            .where(Punicao.discord_id == discord_id, Punicao.ativa.is_(True))
        )
        return int(resultado.scalar_one() or 0)


async def obter_pedido_pendente(discord_id: int) -> SolicitacaoDemissao | None:
    """Busca o pedido pendente mais recente do membro para impedir duplicidade.

    Considera somente o status pendente, de modo que uma pessoa possa solicitar
    novamente depois de uma decisão sem confundir solicitações já finalizadas.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoDemissao)
            .where(
                SolicitacaoDemissao.discord_id == discord_id,
                SolicitacaoDemissao.status == "pendente",
            )
            .order_by(SolicitacaoDemissao.id.desc())
            .limit(1)
        )
        return resultado.scalar_one_or_none()


async def criar_solicitacao(
    *,
    membro: discord.Member,
    motivo: str,
) -> SolicitacaoDemissao:
    """Cria no banco uma solicitação voluntária com o contexto atual do membro.

    Congela cargo, nome e advertências ativas no momento do pedido, pois esses
    dados podem mudar antes da decisão. O motivo é limitado ao campo persistido
    e o registro nasce pendente para ser encaminhado à diretoria.
    """
    advertencias = await contar_advertencias_ativas(membro.id)
    cargo = cargo_atual_hierarquia(membro)
    async with async_session() as sessao:
        registro = SolicitacaoDemissao(
            discord_id=membro.id,
            membro_nome=membro.display_name[:120],
            cargo=cargo,
            tipo_demissao="voluntaria",
            motivo=motivo[:2000],
            data_solicitacao=datetime.now(timezone.utc),
            solicitante_nome=str(membro)[:120],
            advertencias=advertencias,
            status="pendente",
        )
        sessao.add(registro)
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def marcar_mensagem_pedido(
    solicitacao_id: int,
    canal_id: int,
    mensagem_id: int,
) -> None:
    """Vincula ao pedido o card publicado para que a decisão possa localizá-lo.

    Ignora a gravação se a solicitação não existir mais, prevenindo erro numa
    publicação que correu em paralelo com outra ação administrativa.
    """
    async with async_session() as sessao:
        registro = await sessao.get(SolicitacaoDemissao, solicitacao_id)
        if registro is None:
            return
        registro.mensagem_canal_id = canal_id
        registro.mensagem_id = mensagem_id
        registro.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()


async def obter_solicitacao(solicitacao_id: int) -> SolicitacaoDemissao | None:
    """Busca uma solicitação pelo identificador, retornando `None` se foi removida."""
    async with async_session() as sessao:
        return await sessao.get(SolicitacaoDemissao, solicitacao_id)


async def decidir_demissao(
    *,
    solicitacao_id: int,
    aprovada: bool,
    diretor: discord.Member,
) -> tuple[SolicitacaoDemissao | None, bool]:
    """
    Atualiza status. Retorna (registro, foi_decidido_agora).
    Se já estava decidido, foi_decidido_agora=False.
    """
    async with async_session() as sessao:
        registro = await sessao.get(SolicitacaoDemissao, solicitacao_id)
        if registro is None:
            return None, False
        if registro.status != "pendente":
            return registro, False

        registro.status = "aprovada" if aprovada else "negada"
        registro.aprovado_por_id = diretor.id
        registro.aprovado_por_nome = str(diretor)[:120]
        registro.atualizado_em = datetime.now(timezone.utc)
        if aprovada:
            registro.data_efetiva = datetime.now(timezone.utc)
        await sessao.commit()
        await sessao.refresh(registro)
        return registro, True


async def aplicar_cargos_demissao(
    membro: discord.Member,
    *,
    executor: discord.Member,
    motivo: str,
) -> tuple[bool, str]:
    """
    Remove todos os cargos gerenciáveis e deixa apenas Visitantes.
    """
    guilda = membro.guild
    id_visitantes = CARGOS.get("Visitantes")
    role_visitantes = guilda.get_role(id_visitantes) if id_visitantes else None
    if role_visitantes is None:
        return False, "Cargo **Visitantes** não encontrado no config/servidor."

    bot_membro = guilda.me
    if bot_membro is None:
        return False, "Bot sem contexto de membro na guilda."

    ids_manter = {guilda.default_role.id, role_visitantes.id}
    cargos_para_remover: list[discord.Role] = []
    for cargo in list(membro.roles):
        if cargo.id in ids_manter:
            continue
        if cargo.managed:
            continue
        if cargo >= bot_membro.top_role:
            continue
        cargos_para_remover.append(cargo)

    motivo_discord = f"Demissão voluntária — {executor} — {motivo[:80]}"
    try:
        if cargos_para_remover:
            await membro.remove_roles(*cargos_para_remover, reason=motivo_discord)
        if role_visitantes not in membro.roles:
            await membro.add_roles(role_visitantes, reason=motivo_discord)
    except discord.Forbidden:
        return False, "Sem permissão para alterar os cargos do membro."
    except discord.HTTPException as erro:
        return False, f"Falha ao ajustar cargos: {erro}"

    # Remove prefixo [ TAG ] do nick — mesmo padrão da exoneração
    nick_limpo = remover_prefixo_existente(membro.display_name)[:32]
    try:
        nick_atual = membro.nick or membro.display_name
        if nick_limpo and nick_limpo != nick_atual:
            await membro.edit(nick=nick_limpo, reason=motivo_discord)
    except (
        discord.Forbidden,
        discord.HTTPException,
    ) as erro_em_aplicar_cargos_demissao:
        # Nick não é crítico — demissão segue mesmo se falhar
        # Enfeite que falhou: aplicar cargos demissao.
        # A acao principal ja tinha dado certo, entao so registro.
        ignorar_falha_cosmetica(
            erro_em_aplicar_cargos_demissao,
            o_que_falhou="aplicar cargos demissao",
        )

    return True, "Cargos ajustados (restou Visitantes) e prefixo removido do nick."


async def obter_status_usuario(discord_id: int) -> str | None:
    """Lê o status atual em usuarios, ou None se não houver linha."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Usuario.status).where(Usuario.discord_id == int(discord_id))
        )
        valor = resultado.scalar_one_or_none()
        return str(valor) if valor is not None else None


async def marcar_usuario_demitido(discord_id: int) -> bool:
    """
    Define status DEMITIDO em usuarios.

    Devolve True se a linha existia e foi atualizada.
    """
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Usuario).where(Usuario.discord_id == int(discord_id))
        )
        usuario = resultado.scalar_one_or_none()
        if usuario is None:
            return False
        usuario.status = "DEMITIDO"
        await sessao.commit()
        return True


async def limpar_progresso_do_membro(discord_id: int) -> dict[str, int]:
    """
    Zera conquistas de quem saiu sem demissão formal.

    - Apaga estado_plantao (moedas e ciclo em aberto)
    - Apaga log_plantao (banco de horas usado em ranking/promoção)
    - Não apaga laudos/chamadas/recrutamentos operacionais do hospital
      (ficam no histórico da instituição; o status DEMITIDO impede
      reaproveitar a carreira sem novo processo)

    Devolve contagens do que foi limpo.
    """
    id_membro = int(discord_id)
    contagens = {
        "estados_plantao": 0,
        "logs_plantao": 0,
    }
    async with async_session() as sessao:
        try:
            resultado_estado = await sessao.execute(
                select(EstadoPlantao).where(EstadoPlantao.discord_id == id_membro)
            )
            estado = resultado_estado.scalar_one_or_none()
            if estado is not None:
                await sessao.delete(estado)
                contagens["estados_plantao"] = 1

            resultado_logs = await sessao.execute(
                delete(LogPlantao).where(LogPlantao.discord_id == id_membro)
            )
            contagens["logs_plantao"] = int(resultado_logs.rowcount or 0)

            await sessao.commit()
        except SQLAlchemyError as erro_do_banco:
            await sessao.rollback()
            registrador.exception(
                "Falha ao limpar progresso do membro %s: %s",
                id_membro,
                erro_do_banco,
            )
            raise
    return contagens


def _ids_cargos_demitido() -> list[int]:
    """IDs de Visitantes + Exonerado para o snapshot pós-demissão informal."""
    ids: list[int] = []
    id_visitantes = CARGOS.get("Visitantes")
    if id_visitantes:
        ids.append(int(id_visitantes))
    for nome, cargo_id in CARGOS_PUNICOES.items():
        if "exonerado" in str(nome).lower() and cargo_id:
            ids.append(int(cargo_id))
            break
    return ids


def _nomes_cargos_demitido() -> list[str]:
    nomes: list[str] = []
    if CARGOS.get("Visitantes"):
        nomes.append("Visitantes")
    for nome in CARGOS_PUNICOES:
        if "exonerado" in str(nome).lower():
            nomes.append(str(nome))
            break
    return nomes


async def substituir_snapshot_demitido(
    discord_id: int,
    guild_id: int,
    *,
    nickname: str | None = None,
) -> None:
    """
    Troca o snapshot de cargos por Visitantes + Exonerado.

    No rejoin o bot reaplica só esses cargos, sem devolver a hierarquia.
    """
    ids = _ids_cargos_demitido()
    nomes = _nomes_cargos_demitido()
    nick_limpo = None
    if nickname:
        nick_limpo = remover_prefixo_existente(nickname)[:100] or None

    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SnapshotCargosMembro).where(
                SnapshotCargosMembro.discord_id == int(discord_id)
            )
        )
        registro = resultado.scalar_one_or_none()
        texto_ids = json.dumps(ids)
        texto_nomes = json.dumps(nomes, ensure_ascii=False)
        if registro is None:
            sessao.add(
                SnapshotCargosMembro(
                    discord_id=int(discord_id),
                    guild_id=int(guild_id),
                    role_ids=texto_ids,
                    role_names=texto_nomes,
                    nickname=nick_limpo,
                    atualizado_em=agora(),
                )
            )
        else:
            registro.guild_id = int(guild_id)
            registro.role_ids = texto_ids
            registro.role_names = texto_nomes
            registro.nickname = nick_limpo
            registro.atualizado_em = agora()
        await sessao.commit()


async def criar_solicitacao_abandono(
    *,
    discord_id: int,
    membro_nome: str,
    cargo: str,
    motivo: str,
) -> SolicitacaoDemissao:
    """Registra demissão por abandono (saída sem pedir demissão)."""
    advertencias = await contar_advertencias_ativas(discord_id)
    async with async_session() as sessao:
        registro = SolicitacaoDemissao(
            discord_id=int(discord_id),
            membro_nome=membro_nome[:120],
            cargo=cargo[:120] if cargo else None,
            tipo_demissao="abandono",
            motivo=motivo[:2000],
            data_solicitacao=datetime.now(timezone.utc),
            data_efetiva=datetime.now(timezone.utc),
            solicitante_nome="sistema (saída informal)",
            advertencias=advertencias,
            status="pendente_painel",
        )
        sessao.add(registro)
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def processar_demissao_por_abandono(
    membro: discord.Member,
    *,
    motivo: str = "Saiu do Discord sem solicitar demissão formal.",
) -> SolicitacaoDemissao | None:
    """
    Pipeline completo da saída informal de quem estava APROVADO.

    1. Confere status APROVADO no banco
    2. Marca DEMITIDO
    3. Limpa plantão/moedas/horas
    4. Snapshot vira Visitantes + Exonerado
    5. Cria solicitação tipo abandono (aguarda botão do painel in-game)

    Devolve a solicitação criada, ou None se não era APROVADO.
    """
    status_atual = await obter_status_usuario(membro.id)
    if status_atual != "APROVADO":
        return None

    cargo = cargo_atual_hierarquia(membro)
    await marcar_usuario_demitido(membro.id)
    await limpar_progresso_do_membro(membro.id)
    await substituir_snapshot_demitido(
        membro.id,
        membro.guild.id,
        nickname=membro.nick or membro.display_name,
    )
    registro = await criar_solicitacao_abandono(
        discord_id=membro.id,
        membro_nome=membro.display_name,
        cargo=cargo,
        motivo=motivo,
    )
    return registro


async def processar_demissao_admin_fora_do_servidor(
    *,
    discord_id: int,
    guild_id: int,
    membro_nome: str,
    executor: discord.abc.User,
    motivo: str,
) -> SolicitacaoDemissao:
    """
    Demissão manual pelo painel gerenciar-membros (membro já fora do server).

    Mesma limpeza da saída informal: DEMITIDO, progresso zerado, snapshot
    só com Visitantes + Exonerado.
    """
    await marcar_usuario_demitido(discord_id)
    await limpar_progresso_do_membro(discord_id)
    await substituir_snapshot_demitido(
        discord_id,
        guild_id,
        nickname=membro_nome,
    )
    async with async_session() as sessao:
        registro = SolicitacaoDemissao(
            discord_id=int(discord_id),
            membro_nome=membro_nome[:120],
            cargo="—",
            tipo_demissao="admin_fora",
            motivo=motivo[:2000],
            data_solicitacao=datetime.now(timezone.utc),
            data_efetiva=datetime.now(timezone.utc),
            solicitante_nome=str(executor)[:120],
            advertencias=await contar_advertencias_ativas(discord_id),
            status="pendente_painel",
            aprovado_por_id=executor.id,
            aprovado_por_nome=str(executor)[:120],
        )
        sessao.add(registro)
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def marcar_painel_in_game_removido(
    solicitacao_id: int,
    *,
    diretor: discord.Member,
) -> tuple[SolicitacaoDemissao | None, bool]:
    """
    Diretoria confirma que retirou o membro do painel in-game.

    Status passa de pendente_painel → aprovada e libera o log formal.
    """
    async with async_session() as sessao:
        registro = await sessao.get(SolicitacaoDemissao, solicitacao_id)
        if registro is None:
            return None, False
        if registro.status not in ("pendente_painel", "pendente"):
            return registro, False
        registro.status = "aprovada"
        registro.aprovado_por_id = diretor.id
        registro.aprovado_por_nome = str(diretor)[:120]
        registro.data_efetiva = datetime.now(timezone.utc)
        registro.atualizado_em = datetime.now(timezone.utc)
        await sessao.commit()
        await sessao.refresh(registro)
        return registro, True


async def listar_ids_abandono_pendente_painel() -> list[int]:
    """IDs de demissões abandono ainda aguardando botão do painel in-game."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoDemissao.id).where(
                SolicitacaoDemissao.status == "pendente_painel"
            )
        )
        return [int(linha[0]) for linha in resultado.all()]
