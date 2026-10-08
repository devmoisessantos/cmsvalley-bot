"""Serviços de aplicar / remover / consultar punições e exoneração."""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord
from sqlalchemy import select

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
            expira_em=expira_em,
        )
        session.add(punicao_no_banco)
        await session.commit()
        await session.refresh(punicao_no_banco)

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
        ok_exo, msg_exo = await executar_exoneracao(
            guild=guild,
            alvo=membro_atualizado,
            executor=executor,
            id_fivem=id_fivem,
            motivo=motivo,
            links_texto=links_texto,
            punicao_id=punicao_no_banco.id,
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
    5. Registra em CANAL_EXONERACOES
    """
    import json

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

    # Snapshot dos cargos ANTES de tirar — usado se o recurso for aceito
    cargos_antes_ids = [
        cargo.id
        for cargo in alvo.roles
        if cargo.id != guild.default_role.id and not cargo.managed
    ]
    cargos_antes_json = json.dumps(cargos_antes_ids)
    nick_antes = (alvo.nick or alvo.display_name or "")[:100]

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

    # Grava registro de Exonerado no banco quando ainda não veio de aplicar_punicao
    # (ex.: botão em gerenciar-membros) ou quando é automática pela 3ª adv.
    id_do_registro = punicao_id
    links = parse_links(links_texto)
    texto_provas = (links_texto or "").strip() or None
    links_join = "\n".join(links) if links else texto_provas

    precisa_novo_registro = automatica or punicao_id is None
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

    # Snapshot vivo também (rejoin / painel de membros)
    try:
        from src.backup.retrato_de_membros_service import salvar_snapshot_membro

        # Guarda o estado PÓS-exoneração no snapshot contínuo; o recurso
        # usa cargos_antes_json da punição, não este snapshot.
        membro_pos = guild.get_member(alvo.id) or alvo
        await salvar_snapshot_membro(membro_pos)
    except Exception:
        pass

    _ = nick_antes  # reservado para evoluir o recurso com nick original

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

    Se a punição removida for **Exonerado** e houver ``cargos_antes_json``,
    tenta devolver os cargos de produção gravados no momento da exoneração
    (recurso / revogação).

    ``apenas_origem``: se informado (ex.: ``CHAMADA``), só mexe nesses registros.
    """
    import json

    removidos: list[str] = []
    punicao_ids: list[int] = []
    id_fivem: str | None = None
    roles_a_remover: list[discord.Role] = []
    snapshots_para_restaurar: list[list[int]] = []

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

        cargo_ids_marcados: set[int] = set()
        for row in rows:
            row.ativa = False
            row.removida_em = datetime.now(timezone.utc)
            row.removida_por = executor.id
            row.motivo_remocao = (motivo_remocao or "")[:500]
            removidos.append(row.cargo_nome)
            punicao_ids.append(row.id)
            if row.id_fivem and not id_fivem:
                id_fivem = row.id_fivem
            cargo_ids_marcados.add(row.cargo_id)
            if e_cargo_exonerado(cargo_nome=row.cargo_nome, cargo_id=row.cargo_id):
                if row.cargos_antes_json:
                    try:
                        lista_ids = json.loads(row.cargos_antes_json)
                        if isinstance(lista_ids, list):
                            snapshots_para_restaurar.append([int(x) for x in lista_ids])
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass

        await session.commit()

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

    # Recurso de exoneração: devolve cargos de produção salvos no snapshot
    if snapshots_para_restaurar:
        ids_exonerado = {id_cargo_exonerado()} if id_cargo_exonerado() else set()
        ids_punicao = set(CARGOS_PUNICOES.values())
        ids_para_devolver: set[int] = set()
        for lista in snapshots_para_restaurar:
            for role_id in lista:
                if role_id in ids_exonerado:
                    continue
                if role_id in ids_punicao:
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

    lista = ", ".join(f"**{numero.strip()}**" for numero in removidos)
    extra = ""
    if snapshots_para_restaurar:
        extra = " Cargos de produção restaurados (recurso)."
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
