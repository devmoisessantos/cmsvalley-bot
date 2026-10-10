"""Serviços de aplicar / remover / consultar punições e exoneração."""

from __future__ import annotations

import json
import logging
from datetime import (
    datetime,
    timedelta,
    timezone,
)

import discord
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.config import (
    CARGOS,
    CARGOS_PUNICOES,
)
from src.database.conexao import async_session
from src.database.models import (
    Punicao,
    agora,
)
from src.punicoes.punicoes_helpers import (
    e_cargo_exonerado,
    id_cargo_exonerado,
    ids_cargos_advertencia_formal,
    parse_links,
    quantidade_advertencias_formais_no_membro,
)
from src.punicoes.punicoes_logger import (
    registrar_advertencia,
    registrar_exoneracao,
    registrar_log_advertencia,
    registrar_log_remocao,
)
from src.utils.error_handling import ignorar_falha_cosmetica
from src.utils.nickname import remover_prefixo_existente

registrador = logging.getLogger(__name__)

# Prazo padrão da ADV VERBAL quando o chamador não informa expira_em.
DIAS_EXPIRACAO_VERBAL = 3

# Janela para revogar (recurso) a exoneração após ela ser aplicada.
PRAZO_RECURSO_EXONERACAO_HORAS = 48


def _montar_snapshot_exoneracao(
    cargos_antes_ids: list[int],
    nick_antes: str | None,
) -> str:
    """
    Empacota cargos e nick do momento da exoneração em JSON.

    Formato novo: {"cargos": [ids], "nick": "..."}.
    Leitores antigos que esperavam só uma lista ainda são aceitos em
    ``_ler_snapshot_exoneracao``.
    """
    return json.dumps(
        {
            "cargos": list(cargos_antes_ids),
            "nick": (nick_antes or "")[:100],
        }
    )


def _ler_snapshot_exoneracao(
    texto: str | None,
) -> tuple[list[int], str | None]:
    """
    Lê o snapshot gravado na exoneração.

    Aceita lista pura (legado) ou objeto com cargos + nick.
    """
    if not texto:
        return [], None
    try:
        dados = json.loads(texto)
    except (TypeError, ValueError, json.JSONDecodeError):
        return [], None
    if isinstance(dados, list):
        ids = []
        for item in dados:
            try:
                ids.append(int(item))
            except (TypeError, ValueError):
                continue
        return ids, None
    if isinstance(dados, dict):
        lista_bruta = dados.get("cargos") or []
        ids = []
        if isinstance(lista_bruta, list):
            for item in lista_bruta:
                try:
                    ids.append(int(item))
                except (TypeError, ValueError):
                    continue
        nick = dados.get("nick")
        if nick is not None:
            nick = str(nick)[:100]
        return ids, nick
    return [], None


async def aplicar_punicao(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    id_fivem: str,
    cargo_nome: str,
    cargo_id: int,
    motivo: str,
    links_texto: str | None,
    arquivos_provas: list[tuple[bytes, str]] | None = None,
    origem: str = "MANUAL",
    expira_em=None,
) -> tuple[bool, str, Punicao | None]:
    """Aplica cargo, grava no banco, posta em CANAL_ADVERTENCIAS + LOG_PUNICOES.

    Cargos de advertência são **acumulativos** (verbal + Adv 01 + Adv 02…).
    Se o cargo for Exonerado, ou se após a aplicação o membro atingir 3
    advertências formais (Adv 01/02/03), executa a exoneração completa
    (remove todos os cargos, deixa só Exonerado + Visitantes, limpa prefixo
    do nick e registra em CANAL_EXONERACOES).

    ``origem``: MANUAL | CHAMADA | SISTEMA — usado na regularização.
    ``expira_em``: data em que a punição some sozinha (ex.: verbal em 3 dias).
    """
    role = guild.get_role(cargo_id)
    if role is None:
        return (
            False,
            f"❌ Cargo de punição `{cargo_nome}` não encontrado no servidor.",
            None,
        )

    e_exoneracao_direta = e_cargo_exonerado(cargo_nome=cargo_nome, cargo_id=cargo_id)

    try:
        # Acumulativo: só adiciona se ainda não tem; não remove os outros.
        if role not in alvo.roles:
            await alvo.add_roles(role, reason=f"Punição por {executor} — {motivo[:80]}")
    except discord.Forbidden:
        return False, "❌ Sem permissão para adicionar o cargo de punição.", None

    # Recarrega o membro para contar cargos atualizados
    membro_atualizado = guild.get_member(alvo.id) or alvo

    links = parse_links(links_texto)
    texto_provas = (links_texto or "").strip() or None
    links_join = "\n".join(links) if links else texto_provas

    # ADV VERBAL some sozinha após 3 dias. Se o chamador não passou
    # expira_em, calcula aqui para o painel manual e a chamada ficarem iguais.
    data_expiracao = expira_em
    if data_expiracao is None and "verbal" in cargo_nome.lower():
        data_expiracao = datetime.now(timezone.utc) + timedelta(
            days=DIAS_EXPIRACAO_VERBAL
        )

    try:
        async with async_session() as session:
            punicao_no_banco = Punicao(
                discord_id=alvo.id,
                id_fivem=id_fivem,
                cargo_id=cargo_id,
                cargo_nome=cargo_nome,
                motivo=motivo[:1500],
                links=links_join[:2000] if links_join else None,
                executor_id=executor.id,
                ativa=True,
                criada_em=agora(),
                origem=(origem or "MANUAL")[:30],
                expira_em=data_expiracao,
            )
            session.add(punicao_no_banco)
            await session.commit()
            await session.refresh(punicao_no_banco)
    except SQLAlchemyError as erro_do_banco:
        registrador.exception(
            "Falha ao gravar punição de %s: %s", alvo.id, erro_do_banco
        )
        return (
            False,
            "❌ Não consegui salvar a punição no banco agora. "
            "O cargo pode ter sido aplicado; confira e tente de novo.",
            None,
        )

    # Exoneração direta: não usa CANAL_ADVERTENCIAS — só CANAL_EXONERACOES
    if e_exoneracao_direta:
        ok_exo, msg_exo = await executar_exoneracao(
            guild=guild,
            alvo=membro_atualizado,
            executor=executor,
            id_fivem=id_fivem,
            motivo=motivo,
            links_texto=links_texto,
            punicao_id=punicao_no_banco.id,
            automatica=False,
            arquivos_provas=arquivos_provas,
        )
        await registrar_log_advertencia(
            guild=guild,
            alvo=membro_atualizado,
            executor=executor,
            id_fivem=id_fivem,
            cargo_role=role,
            motivo=motivo,
            punicao_id=punicao_no_banco.id,
            msg_advertencia=None,
        )
        if ok_exo:
            return True, f"✅ {msg_exo}", punicao_no_banco
        return (
            True,
            f"✅ Cargo **{cargo_nome}** aplicado em {alvo.mention}.\n⚠️ {msg_exo}",
            punicao_no_banco,
        )

    # 1) Registro público (CANAL_ADVERTENCIAS) + tópico de provas + DM
    msg_adv, thread = await registrar_advertencia(
        guild=guild,
        alvo=membro_atualizado,
        executor=executor,
        id_fivem=id_fivem,
        cargo_role=role,
        motivo=motivo,
        links=links,
        punicao_id=punicao_no_banco.id,
        texto_provas=texto_provas,
        arquivos_provas=arquivos_provas,
    )

    if msg_adv:
        try:
            async with async_session() as session:
                resultado_da_consulta = await session.execute(
                    select(Punicao).where(Punicao.id == punicao_no_banco.id)
                )
                row = resultado_da_consulta.scalar_one()
                row.channel_id = msg_adv.channel.id
                row.message_id = msg_adv.id
                if thread:
                    row.thread_id = thread.id
                await session.commit()
        except SQLAlchemyError as erro_do_banco:
            registrador.warning(
                "Punição #%s gravada, mas falhou ao salvar ids da mensagem: %s",
                punicao_no_banco.id,
                erro_do_banco,
            )

    # 2) Log interno (LOG_PUNICOES)
    await registrar_log_advertencia(
        guild=guild,
        alvo=membro_atualizado,
        executor=executor,
        id_fivem=id_fivem,
        cargo_role=role,
        motivo=motivo,
        punicao_id=punicao_no_banco.id,
        msg_advertencia=msg_adv,
    )

    # 3) 3ª advertência formal → exoneração automática
    quantidade_formais = quantidade_advertencias_formais_no_membro(membro_atualizado)
    automatica_por_limite = (
        cargo_id in ids_cargos_advertencia_formal() and quantidade_formais >= 3
    )

    mensagem_extra = ""
    if automatica_por_limite:
        # Sem reutilizar o ID da Adv 03: a exoneração cria registro próprio.
        ok_exo, msg_exo = await executar_exoneracao(
            guild=guild,
            alvo=membro_atualizado,
            executor=executor,
            id_fivem=id_fivem,
            motivo=motivo,
            links_texto=links_texto,
            punicao_id=None,
            automatica=True,
        )
        if ok_exo:
            mensagem_extra = f"\n{msg_exo}"
        else:
            mensagem_extra = f"\n⚠️ Punição aplicada, mas a exoneração falhou: {msg_exo}"

    return (
        True,
        f"✅ Punição **{cargo_nome}** aplicada em {alvo.mention}.{mensagem_extra}",
        punicao_no_banco,
    )


async def executar_exoneracao(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    id_fivem: str,
    motivo: str,
    links_texto: str | None = None,
    punicao_id: int | None = None,
    automatica: bool = False,
    arquivos_provas: list[tuple[bytes, str]] | None = None,
) -> tuple[bool, str]:
    """
    Exoneração completa:
    1. Guarda JSON dos cargos atuais (para recurso / revogação)
    2. Remove TODOS os cargos (exceto @everyone e cargos gerenciados)
    3. Deixa apenas Exonerado + Visitantes
    4. Remove o prefixo [ TAG ] do nick → fica Nome | ID
    5. Zera horas de plantão e saldo de moedas
    6. Registra em CANAL_EXONERACOES
    """
    id_exonerado = id_cargo_exonerado()
    id_visitantes = CARGOS.get("Visitantes")

    if id_exonerado is None:
        return False, "❌ Cargo Exonerado não configurado em CARGOS_PUNICOES."

    role_exonerado = guild.get_role(id_exonerado)
    role_visitantes = guild.get_role(id_visitantes) if id_visitantes else None

    if role_exonerado is None:
        return False, "❌ Cargo Exonerado não encontrado no servidor."

    bot_member = guild.me
    if bot_member is None:
        return False, "❌ Bot sem contexto de membro na guilda."

    # Snapshot dos cargos e do nick ANTES de tirar — usado no recurso
    cargos_antes_ids = [
        cargo.id
        for cargo in alvo.roles
        if cargo.id != guild.default_role.id and not cargo.managed
    ]
    nick_antes = (alvo.nick or alvo.display_name or "")[:100]
    cargos_antes_json = _montar_snapshot_exoneracao(
        cargos_antes_ids,
        nick_antes,
    )

    # Cargos que devem permanecer
    ids_para_manter: set[int] = {guild.default_role.id, id_exonerado}
    if id_visitantes:
        ids_para_manter.add(id_visitantes)

    cargos_para_remover: list[discord.Role] = []
    for cargo in list(alvo.roles):
        if cargo.id in ids_para_manter:
            continue
        if cargo.managed:
            continue
        if cargo >= bot_member.top_role:
            continue
        cargos_para_remover.append(cargo)

    motivo_discord = f"Exoneração por {executor} — {motivo[:80]}"

    try:
        if cargos_para_remover:
            await alvo.remove_roles(*cargos_para_remover, reason=motivo_discord)

        cargos_para_adicionar: list[discord.Role] = []
        if role_exonerado not in alvo.roles:
            cargos_para_adicionar.append(role_exonerado)
        if role_visitantes is not None and role_visitantes not in alvo.roles:
            cargos_para_adicionar.append(role_visitantes)
        if cargos_para_adicionar:
            await alvo.add_roles(*cargos_para_adicionar, reason=motivo_discord)
    except discord.Forbidden:
        return False, "❌ Sem permissão para alterar cargos do membro."
    except discord.HTTPException as erro:
        return False, f"❌ Falha ao ajustar cargos: {erro}"

    # Nick: remove [ TAG ], mantém o restante (ex.: "Nome | 12345")
    nick_limpo = remover_prefixo_existente(alvo.display_name)[:32]
    try:
        nick_atual = alvo.nick or alvo.display_name
        if nick_limpo and nick_limpo != nick_atual:
            await alvo.edit(nick=nick_limpo, reason=motivo_discord)
    except (discord.Forbidden, discord.HTTPException) as erro_em_executar_exoneracao:
        ignorar_falha_cosmetica(
            erro_em_executar_exoneracao,
            o_que_falhou="executar exoneracao",
        )

    # Zera plantão e moedas só na exoneração (na revogação eles permanecem).
    try:
        from src.membros.membros_service import (
            ajustar_horas_plantao,
            zerar_ciclo_plantao,
        )
        from src.plantao.carteira_service import zerar_saldo_moedas

        await zerar_ciclo_plantao(alvo.id)
        await ajustar_horas_plantao(
            alvo.id,
            segundos_absolutos=0,
            executor_id=executor.id,
            motivo="Zerar plantão na exoneração",
        )
        await zerar_saldo_moedas(
            alvo.id,
            motivo="Zerar moedas na exoneração",
        )
    except Exception as erro_reset_plantao:
        registrador.exception(
            "Exoneração de %s: falha ao zerar plantão/moedas: %s",
            alvo.id,
            erro_reset_plantao,
        )

    # Grava registro de Exonerado no banco quando ainda não veio de aplicar_punicao
    # (ex.: botão em gerenciar-membros) ou quando é automática pela 3ª adv.
    id_do_registro = punicao_id
    links = parse_links(links_texto)
    texto_provas = (links_texto or "").strip() or None
    links_join = "\n".join(links) if links else texto_provas

    precisa_novo_registro = automatica or punicao_id is None
    try:
        if precisa_novo_registro:
            async with async_session() as session:
                punicao_no_banco = Punicao(
                    discord_id=alvo.id,
                    id_fivem=id_fivem,
                    cargo_id=id_exonerado,
                    cargo_nome=next(
                        (
                            nome
                            for nome, id_do_cargo in CARGOS_PUNICOES.items()
                            if id_do_cargo == id_exonerado
                        ),
                        "🚫┇Exonerado",
                    ),
                    motivo=(
                        motivo[:1500]
                        if motivo
                        else (
                            "Exoneração automática (3ª advertência)"
                            if automatica
                            else "Exoneração manual"
                        )
                    ),
                    links=links_join[:2000] if links_join else None,
                    executor_id=executor.id,
                    ativa=True,
                    criada_em=agora(),
                    origem="SISTEMA" if automatica else "MANUAL",
                    cargos_antes_json=cargos_antes_json,
                )
                session.add(punicao_no_banco)
                await session.commit()
                await session.refresh(punicao_no_banco)
                id_do_registro = punicao_no_banco.id
        elif punicao_id is not None:
            # Já existia registro (veio de aplicar_punicao) — só grava o snapshot
            async with async_session() as session:
                resultado = await session.execute(
                    select(Punicao).where(Punicao.id == punicao_id)
                )
                row = resultado.scalar_one_or_none()
                if row is not None:
                    row.cargos_antes_json = cargos_antes_json
                    await session.commit()
    except SQLAlchemyError as erro_do_banco:
        registrador.exception(
            "Falha ao gravar registro de exoneração de %s: %s",
            alvo.id,
            erro_do_banco,
        )
        return (
            False,
            "❌ Cargos ajustados, mas não consegui salvar a exoneração no banco.",
        )

    # Snapshot vivo também (rejoin / painel de membros)
    try:
        from src.backup.retrato_de_membros_service import salvar_snapshot_membro

        # Guarda o estado PÓS-exoneração no snapshot contínuo; o recurso
        # usa cargos_antes_json da punição, não este snapshot.
        membro_pos = guild.get_member(alvo.id) or alvo
        await salvar_snapshot_membro(membro_pos)
    except Exception as erro_do_snapshot:
        registrador.warning(
            "Falha ao salvar snapshot pós-exoneração de %s: %s",
            alvo.id,
            erro_do_snapshot,
        )

    msg_exo, _thread = await registrar_exoneracao(
        guild=guild,
        alvo=alvo,
        executor=executor,
        id_fivem=id_fivem or "—",
        motivo=motivo,
        links=links,
        punicao_id=id_do_registro,
        texto_provas=texto_provas,
        automatica=automatica,
        arquivos_provas=arquivos_provas,
    )

    if msg_exo and id_do_registro is not None:
        async with async_session() as session:
            resultado_da_consulta = await session.execute(
                select(Punicao).where(Punicao.id == id_do_registro)
            )
            row = resultado_da_consulta.scalar_one_or_none()
            if row is not None:
                row.channel_id = msg_exo.channel.id
                row.message_id = msg_exo.id
                await session.commit()

    from src.utils.notificacao import notificar_dm_exoneracao

    await notificar_dm_exoneracao(
        alvo=alvo,
        executor=executor,
        id_fivem=id_fivem or "—",
        motivo=motivo or "Exoneração",
        automatica=automatica,
        msg_log=msg_exo,
    )

    origem = "automática (3ª advertência)" if automatica else "manual"
    return True, f"⛔ Exoneração {origem} concluída em {alvo.mention}."


async def remover_punicao(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    cargo_id: int | None = None,
    punicao_id: int | None = None,
    motivo_remocao: str | None = None,
    apenas_origem: str | None = None,
) -> tuple[bool, str]:
    """
    Remove cargo(s) de punição, marca registros inativos e loga em LOG_PUNICOES.

    Se a punição removida for **Exonerado** (recurso / revogação):
    - só é permitido em até ``PRAZO_RECURSO_EXONERACAO_HORAS`` após a
      exoneração; fora do prazo a operação é recusada com aviso
    - zera TODAS as advertências ativas no banco
    - tira cargos de punição, Exonerado e Visitantes no Discord
    - devolve os cargos de produção e o nick do snapshot
    - plantão e moedas **não** são alterados (só zeravam na exoneração)

    ``apenas_origem``: se informado (ex.: ``CHAMADA``), só mexe nesses registros
    (não aplica o pacote completo de recurso).
    """
    removidos: list[str] = []
    punicao_ids: list[int] = []
    id_fivem: str | None = None
    roles_a_remover: list[discord.Role] = []
    ids_cargos_snapshot: list[int] = []
    nick_para_restaurar: str | None = None
    e_recurso_de_exoneracao = False

    async with async_session() as session:
        filtros = [
            Punicao.discord_id == alvo.id,
            Punicao.ativa.is_(True),
        ]
        if punicao_id is not None:
            filtros.append(Punicao.id == punicao_id)
        elif cargo_id is not None:
            filtros.append(Punicao.cargo_id == cargo_id)
        if apenas_origem is not None:
            filtros.append(Punicao.origem == apenas_origem)

        resultado_da_consulta = await session.execute(select(Punicao).where(*filtros))
        rows = list(resultado_da_consulta.scalars().all())

        if not rows:
            if cargo_id and apenas_origem is None:
                role = guild.get_role(cargo_id)
                if role and role in alvo.roles:
                    try:
                        await alvo.remove_roles(
                            role,
                            reason=(
                                f"Remoção de punição por {executor} — "
                                f"{motivo_remocao or 'sem motivo'}"
                            ),
                        )
                    except discord.Forbidden:
                        return (
                            False,
                            "❌ Sem permissão para remover os cargos de punição.",
                        )
                    await registrar_log_remocao(
                        guild=guild,
                        alvo=alvo,
                        executor=executor,
                        cargos_removidos=[role.name],
                        motivo_remocao=motivo_remocao,
                    )
                    return (
                        True,
                        f"✅ Cargo de punição removido de {alvo.mention}: "
                        f"{role.mention}",
                    )
            return False, "❌ Este membro não possui punições ativas registradas."

        # Revogar Exonerado = recurso completo, só dentro do prazo de 48h.
        if apenas_origem is None:
            registro_exonerado = None
            for row in rows:
                if e_cargo_exonerado(
                    cargo_nome=row.cargo_nome,
                    cargo_id=row.cargo_id,
                ):
                    e_recurso_de_exoneracao = True
                    registro_exonerado = row
                    lista_ids, nick_salvo = _ler_snapshot_exoneracao(
                        row.cargos_antes_json
                    )
                    if lista_ids:
                        ids_cargos_snapshot = lista_ids
                    if nick_salvo:
                        nick_para_restaurar = nick_salvo
            if e_recurso_de_exoneracao and registro_exonerado is not None:
                data_da_exoneracao = registro_exonerado.criada_em
                if data_da_exoneracao is not None:
                    if data_da_exoneracao.tzinfo is None:
                        data_da_exoneracao = data_da_exoneracao.replace(
                            tzinfo=timezone.utc
                        )
                    limite = data_da_exoneracao + timedelta(
                        hours=PRAZO_RECURSO_EXONERACAO_HORAS
                    )
                    agora_utc = datetime.now(timezone.utc)
                    if agora_utc > limite:
                        horas = PRAZO_RECURSO_EXONERACAO_HORAS
                        return (
                            False,
                            "❌ O prazo de recurso da exoneração expirou. "
                            f"A revogação só é permitida em até **{horas} horas** "
                            f"após a exoneração "
                            f"(<t:{int(data_da_exoneracao.timestamp())}:f> → "
                            f"<t:{int(limite.timestamp())}:f>).",
                        )
                resultado_todas = await session.execute(
                    select(Punicao).where(
                        Punicao.discord_id == alvo.id,
                        Punicao.ativa.is_(True),
                    )
                )
                rows = list(resultado_todas.scalars().all())

        cargo_ids_marcados: set[int] = set()
        agora_utc = datetime.now(timezone.utc)
        for row in rows:
            row.ativa = False
            row.removida_em = agora_utc
            row.removida_por = executor.id
            if e_recurso_de_exoneracao and not e_cargo_exonerado(
                cargo_nome=row.cargo_nome,
                cargo_id=row.cargo_id,
            ):
                texto_motivo = (
                    motivo_remocao
                    or "Reset automático na revogação da exoneração"
                )
            else:
                texto_motivo = motivo_remocao or ""
            row.motivo_remocao = texto_motivo[:500]
            removidos.append(row.cargo_nome)
            punicao_ids.append(row.id)
            if row.id_fivem and not id_fivem:
                id_fivem = row.id_fivem
            cargo_ids_marcados.add(row.cargo_id)

        try:
            await session.commit()
        except SQLAlchemyError as erro_do_banco:
            await session.rollback()
            registrador.exception(
                "Falha ao marcar punições inativas de %s: %s",
                alvo.id,
                erro_do_banco,
            )
            return False, "❌ Não consegui atualizar as punições no banco agora."

        for cid in cargo_ids_marcados:
            r2 = await session.execute(
                select(Punicao).where(
                    Punicao.discord_id == alvo.id,
                    Punicao.ativa.is_(True),
                    Punicao.cargo_id == cid,
                )
            )
            if r2.scalar_one_or_none() is None:
                role = guild.get_role(cid)
                if role and role in alvo.roles:
                    roles_a_remover.append(role)

    # No recurso, tira também Visitantes e qualquer cargo de punição residual.
    if e_recurso_de_exoneracao:
        id_visitantes = CARGOS.get("Visitantes")
        ids_punicao = set(CARGOS_PUNICOES.values())
        for role in list(alvo.roles):
            if role.id == guild.default_role.id:
                continue
            if role.managed:
                continue
            if id_visitantes and role.id == id_visitantes:
                if role not in roles_a_remover:
                    roles_a_remover.append(role)
                continue
            if role.id in ids_punicao and role not in roles_a_remover:
                roles_a_remover.append(role)

    if roles_a_remover:
        try:
            await alvo.remove_roles(
                *roles_a_remover,
                reason=(
                    f"Remoção de punição por {executor} — "
                    f"{motivo_remocao or 'sem motivo'}"
                ),
            )
        except discord.Forbidden:
            return False, "❌ Sem permissão para remover os cargos de punição."

    # Recurso: devolve cargos de produção do snapshot (sem punição / Exonerado).
    if e_recurso_de_exoneracao and ids_cargos_snapshot:
        ids_exonerado = {id_cargo_exonerado()} if id_cargo_exonerado() else set()
        ids_punicao = set(CARGOS_PUNICOES.values())
        id_visitantes = CARGOS.get("Visitantes")
        ids_para_devolver: set[int] = set()
        for role_id in ids_cargos_snapshot:
            if role_id in ids_exonerado:
                continue
            if role_id in ids_punicao:
                continue
            if id_visitantes and role_id == id_visitantes:
                continue
            ids_para_devolver.add(role_id)
        cargos_para_devolver = []
        for role_id in ids_para_devolver:
            role = guild.get_role(role_id)
            if role is not None and role not in alvo.roles:
                cargos_para_devolver.append(role)
        if cargos_para_devolver:
            try:
                await alvo.add_roles(
                    *cargos_para_devolver,
                    reason=(
                        f"Recurso de exoneração aceito por {executor} — "
                        f"{motivo_remocao or 'revogação'}"
                    ),
                )
            except discord.Forbidden:
                return (
                    False,
                    "❌ Punição removida, mas sem permissão para restaurar cargos.",
                )
            except discord.HTTPException as erro_restore:
                return (
                    False,
                    f"❌ Punição removida, mas falha ao restaurar cargos: "
                    f"{erro_restore}",
                )

    # Recurso: devolve o nick salvo no snapshot.
    # Plantão e moedas não mudam aqui — só foram zerados na exoneração.
    if e_recurso_de_exoneracao and nick_para_restaurar:
        try:
            nick_limpo = nick_para_restaurar[:32]
            nick_atual = alvo.nick or alvo.display_name or ""
            if nick_limpo and nick_limpo != nick_atual:
                await alvo.edit(
                    nick=nick_limpo,
                    reason=(
                        f"Recurso de exoneração — nick restaurado por "
                        f"{executor}"
                    ),
                )
        except (discord.Forbidden, discord.HTTPException) as erro_nick:
            ignorar_falha_cosmetica(
                erro_nick,
                o_que_falhou="restaurar nick no recurso de exoneracao",
            )

    await registrar_log_remocao(
        guild=guild,
        alvo=alvo,
        executor=executor,
        cargos_removidos=removidos,
        motivo_remocao=motivo_remocao,
        punicao_ids=punicao_ids,
        id_fivem=id_fivem,
    )

    from src.utils.notificacao import notificar_dm_remocao_punicao

    await notificar_dm_remocao_punicao(
        alvo=alvo,
        executor=executor,
        cargos_removidos=removidos,
        motivo_remocao=motivo_remocao,
    )

    lista = ", ".join(f"**{nome.strip()}**" for nome in removidos)
    extra = ""
    if e_recurso_de_exoneracao:
        extra = (
            " Recurso aplicado: cargos de produção e nick restaurados, "
            "advertências zeradas, Visitantes/Exonerado removidos. "
            "Plantão e moedas permanecem como estavam após a exoneração."
        )
    return True, f"✅ Punição removida de {alvo.mention}: {lista}.{extra}"


async def listar_punicoes_membro(
    discord_id: int, apenas_ativas: bool = False
) -> list[Punicao]:
    """Retorna o histórico disciplinar em ordem recente, com filtro opcional de ativas.

    Usa o ID permanente do Discord para que mudanças de nome não afastem registros do
    membro. O filtro existe para fluxos de remoção não oferecerem punições já baixadas,
    sem ocultar o histórico completo nas consultas administrativas.
    """
    async with async_session() as session:
        stmt = select(Punicao).where(Punicao.discord_id == discord_id)
        if apenas_ativas:
            stmt = stmt.where(Punicao.ativa.is_(True))
        stmt = stmt.order_by(Punicao.criada_em.desc())
        resultado_da_consulta = await session.execute(stmt)
        return list(resultado_da_consulta.scalars().all())
