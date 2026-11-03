"""Regras de rebaixamento hospitalar (punição).

Independente do fluxo de promoção. Grava HistoricoPunicao + Punicao(tipo=REBAIXAMENTO).
"""

from __future__ import annotations

import json
import logging
from datetime import (
    datetime,
    timedelta,
    timezone,
)

import discord
from sqlalchemy import select, update

from src.config import (
    CARGOS,
    CARGOS_HIERARQUIA,
    CARGO_DIRETOR,
    CARGO_RESP_DOUTOR,
    CARGO_RESP_INSTRUTOR,
    CARGO_RESP_PSICOLOGO,
    CARGO_RESP_RECRUTAMENTO,
)
from src.database.conexao import async_session
from src.database.models import (
    HistoricoPunicao,
    Punicao,
    SolicitacaoPromocao,
    Usuario,
    agora,
)
from src.utils.nickname import aplicar_prefixo

registrador = logging.getLogger(__name__)

# Quem pode iniciar rebaixamento (Diretor++ — Supervisor e Vice-Diretor fora).
CARGOS_PODE_REBAIXAR: tuple[str, ...] = (
    "Responsavel HP",
    "👑 | RESPONSÁVEL GERAL",
    "👑 |  DIRETOR GERAL",
    "👑 |  VICE DIRETOR GERAL",
    "🔍・COORDENADOR",
    CARGO_RESP_INSTRUTOR,
    CARGO_RESP_RECRUTAMENTO,
    CARGO_RESP_PSICOLOGO,
    CARGO_RESP_DOUTOR,
    CARGO_DIRETOR,
)

# Cargos de área removidos em todo rebaixamento.
CARGOS_AREA_REMOVER: tuple[str, ...] = (
    CARGO_RESP_DOUTOR,
    CARGO_RESP_PSICOLOGO,
    CARGO_RESP_RECRUTAMENTO,
    CARGO_RESP_INSTRUTOR,
    "👑・Responsável Destaque・👑",
)

ID_EQUIPE_DIRETORIA_GERAL = 1491000030138732656

DIAS_BLOQUEIO_OPCOES: tuple[int, ...] = (7, 15, 21)

def membro_pode_rebaixar(membro: discord.Member) -> bool:
    """True se o executor tem algum cargo de Diretor++."""
    if membro.guild_permissions.administrator:
        return True
    nomes = {cargo.name for cargo in membro.roles}
    return any(nome in nomes for nome in CARGOS_PODE_REBAIXAR)


def membro_e_diretoria_geral(membro: discord.Member) -> bool:
    """Livre (qualquer cargo inferior) só para EQUIPE・DIRETORIA GERAL."""
    if membro.guild_permissions.administrator:
        return True
    return any(cargo.id == ID_EQUIPE_DIRETORIA_GERAL for cargo in membro.roles)


def membro_pode_revogar_rebaixamento(membro: discord.Member) -> bool:
    """
    Quem pode revogar rebaixamento ativo.

    Apenas: Administrador Discord, Responsavel HP, EQUIPE・DIRETORIA GERAL.
    """
    if membro.guild_permissions.administrator:
        return True
    nomes = {cargo.name for cargo in membro.roles}
    if "Responsavel HP" in nomes:
        return True
    return any(cargo.id == ID_EQUIPE_DIRETORIA_GERAL for cargo in membro.roles)


def cargo_hierarquia_do_membro(membro: discord.Member) -> str | None:
    """Maior cargo hospitalar do membro (ordem de CARGOS_HIERARQUIA)."""
    nomes = {cargo.name for cargo in membro.roles}
    for nome in CARGOS_HIERARQUIA:
        if nome in nomes:
            return nome
    return None


def indice_hierarquia(nome_cargo: str) -> int | None:
    """Índice em CARGOS_HIERARQUIA ou None se não for cargo da hierarquia."""
    try:
        return CARGOS_HIERARQUIA.index(nome_cargo)
    except ValueError:
        return None


def indice_hierarquia_do_executor(membro: discord.Member) -> int | None:
    """
    Índice do maior cargo do executor na hierarquia.

    Administrador Discord = -1 (acima de todos).
    None = sem cargo na hierarquia e sem admin.
    """
    if membro.guild_permissions.administrator:
        return -1
    cargo = cargo_hierarquia_do_membro(membro)
    if cargo is None:
        return None
    return indice_hierarquia(cargo)


def cargo_um_degrau_abaixo(nome_cargo: str) -> str | None:
    """Próximo cargo abaixo na hierarquia, ou None se já for o mais baixo."""
    indice = indice_hierarquia(nome_cargo)
    if indice is None:
        return None
    if indice >= len(CARGOS_HIERARQUIA) - 1:
        return None
    return CARGOS_HIERARQUIA[indice + 1]


def alvo_pode_ser_rebaixado(
    alvo: discord.Member,
    executor: discord.Member,
) -> tuple[bool, str]:
    """
    O alvo só pode ser rebaixado se estiver **estritamente abaixo**
    do executor na hierarquia hospitalar.

    - Mesmo cargo → bloqueado
    - Cargo acima do executor → bloqueado
    - Administrador pode rebaixar qualquer um abaixo do topo
    - Quem rebaixa precisa ser Diretor++ (checado em membro_pode_rebaixar)
    """
    if not membro_pode_rebaixar(executor):
        return False, "Você não tem permissão para rebaixar (apenas Diretor++)."

    cargo_alvo = cargo_hierarquia_do_membro(alvo)
    if cargo_alvo is None:
        return False, "O membro não possui cargo na hierarquia hospitalar."

    indice_alvo = indice_hierarquia(cargo_alvo)
    if indice_alvo is None:
        return False, f"Cargo `{cargo_alvo}` fora da hierarquia."

    if indice_alvo >= len(CARGOS_HIERARQUIA) - 1:
        return False, f"**{cargo_alvo}** já é o cargo mais baixo da hierarquia."

    indice_executor = indice_hierarquia_do_executor(executor)
    if indice_executor is None:
        return False, "Você não possui cargo na hierarquia hospitalar."

    # Índice menor = cargo mais alto. Só gerencia quem está abaixo (índice maior).
    if indice_alvo <= indice_executor:
        cargo_exec = cargo_hierarquia_do_membro(executor) or "Administrador"
        return False, (
            f"Não é permitido rebaixar **{cargo_alvo}**: "
            f"seu cargo (**{cargo_exec}**) só gerencia cargos **abaixo** na hierarquia "
            f"(mesmo nível ou acima bloqueados)."
        )

    return True, cargo_alvo


def listar_cargos_inferiores(cargo_atual: str) -> list[str]:
    """Todos os cargos estritamente abaixo do atual (para modo livre)."""
    indice = indice_hierarquia(cargo_atual)
    if indice is None:
        return []
    return list(CARGOS_HIERARQUIA[indice + 1 :])


def _resolver_role(guild: discord.Guild, nome: str) -> discord.Role | None:
    cargo_id = CARGOS.get(nome) or 0
    if cargo_id:
        role = guild.get_role(int(cargo_id))
        if role is not None:
            return role
    for role in guild.roles:
        if role.name == nome:
            return role
    return None


async def cancelar_solicitacoes_promocao_pendentes(discord_id: int) -> int:
    """Marca pedidos de promoção pendentes como CANCELADA. Retorna quantos."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(SolicitacaoPromocao).where(
                SolicitacaoPromocao.discord_id == discord_id,
                SolicitacaoPromocao.status == "PENDENTE",
            )
        )
        pendentes = list(resultado.scalars().all())
        for registro in pendentes:
            registro.status = "CANCELADA"
            registro.motivo_reprovacao = "Cancelada automaticamente por rebaixamento."
            registro.atualizado_em = agora()
        if pendentes:
            await sessao.commit()
        return len(pendentes)


async def obter_bloqueio_promocao(
    discord_id: int,
) -> datetime | None:
    """Data até quando o membro está bloqueado de solicitar promoção."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Usuario).where(Usuario.discord_id == discord_id)
        )
        usuario = resultado.scalar_one_or_none()
        if usuario is None:
            return None
        return usuario.bloqueio_promocao_ate


async def membro_esta_bloqueado_promocao(discord_id: int) -> tuple[bool, datetime | None]:
    """(bloqueado?, ate). Compara com agora UTC."""
    ate = await obter_bloqueio_promocao(discord_id)
    if ate is None:
        return False, None
    agora_utc = datetime.now(timezone.utc)
    if ate.tzinfo is None:
        ate = ate.replace(tzinfo=timezone.utc)
    if ate > agora_utc:
        return True, ate
    return False, ate


async def obter_metas_baseline(discord_id: int) -> dict[str, int]:
    """Baseline de metas gravado no rebaixamento (vazio se não houver)."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Usuario).where(Usuario.discord_id == discord_id)
        )
        usuario = resultado.scalar_one_or_none()
        if usuario is None or not usuario.metas_baseline_json:
            return {}
        try:
            dados = json.loads(usuario.metas_baseline_json)
            return {str(chave): int(valor) for chave, valor in dados.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}


async def _garantir_usuario(sessao, discord_id: int) -> Usuario:
    resultado = await sessao.execute(
        select(Usuario).where(Usuario.discord_id == discord_id)
    )
    usuario = resultado.scalar_one_or_none()
    if usuario is not None:
        return usuario
    usuario = Usuario(discord_id=discord_id, status="APROVADO", ja_foi_aprovado=True)
    sessao.add(usuario)
    await sessao.flush()
    return usuario


async def aplicar_rebaixamento(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    cargo_para: str,
    motivo: str,
    dias_bloqueio: int,
    modo: str,
    metas_atuais: dict[str, int] | None = None,
) -> tuple[bool, str]:
    """
    Aplica o rebaixamento completo.

    - Remove cargo atual (e superiores hospitalares do membro) + cargos de área
    - Adiciona cargo_para
    - Atualiza tag do nick
    - Cancela promoção pendente
    - Grava Punicao(tipo=REBAIXAMENTO) + HistoricoPunicao
    - Seta bloqueio_promocao_ate e metas_baseline_json
    """
    if dias_bloqueio not in DIAS_BLOQUEIO_OPCOES:
        return False, f"Dias de bloqueio inválidos: {dias_bloqueio}."

    ok, detalhe = alvo_pode_ser_rebaixado(alvo, executor)
    if not ok:
        return False, detalhe
    cargo_de = detalhe

    indice_destino = indice_hierarquia(cargo_para)
    indice_origem = indice_hierarquia(cargo_de)
    if indice_destino is None:
        return False, f"Cargo destino `{cargo_para}` não está na hierarquia."
    if indice_origem is None or indice_destino <= indice_origem:
        return False, "O cargo destino precisa ser **inferior** ao cargo atual."

    if modo == "livre" and not membro_e_diretoria_geral(executor):
        return False, "Apenas **EQUIPE・DIRETORIA GERAL** pode escolher cargo livre."

    if modo == "um_degrau":
        esperado = cargo_um_degrau_abaixo(cargo_de)
        if esperado is None or esperado != cargo_para:
            return False, "No modo um degrau o destino deve ser o cargo imediatamente abaixo."

    role_destino = _resolver_role(guild, cargo_para)
    if role_destino is None:
        return False, f"Cargo `{cargo_para}` não encontrado no servidor."

    # Roles a remover: todos da hierarquia com índice < destino (mais altos
    # que o novo nível, inclusive o atual) + cargos de área.
    roles_remover: list[discord.Role] = []
    nomes_removidos: list[str] = []
    for nome in CARGOS_HIERARQUIA[:indice_destino]:
        role = _resolver_role(guild, nome)
        if role is not None and role in alvo.roles:
            roles_remover.append(role)
            nomes_removidos.append(nome)
    for nome in CARGOS_AREA_REMOVER:
        role = _resolver_role(guild, nome)
        if role is not None and role in alvo.roles and role not in roles_remover:
            roles_remover.append(role)
            nomes_removidos.append(nome)

    motivo_discord = f"Rebaixamento por {executor} — {motivo[:200]}"
    try:
        if roles_remover:
            await alvo.remove_roles(*roles_remover, reason=motivo_discord)
        if role_destino not in alvo.roles:
            await alvo.add_roles(role_destino, reason=motivo_discord)
    except discord.Forbidden:
        return False, (
            "Sem permissão para alterar cargos deste membro "
            "(hierarquia do bot abaixo do cargo?)."
        )
    except discord.HTTPException as erro:
        return False, f"Erro Discord ao alterar cargos: {erro}"

    # Tag do nick = cargo de destino
    try:
        nick_atual = alvo.nick or alvo.display_name or alvo.name
        novo_nick = aplicar_prefixo(nick_atual, cargo_para)
        if novo_nick and novo_nick != alvo.nick:
            await alvo.edit(nick=novo_nick[:32], reason=motivo_discord)
    except discord.Forbidden:
        registrador.warning(
            "Rebaixamento OK mas sem permissão de nick para %s", alvo.id
        )
    except discord.HTTPException as erro:
        registrador.warning("Falha ao editar nick no rebaixamento de %s: %s", alvo.id, erro)

    canceladas = await cancelar_solicitacoes_promocao_pendentes(alvo.id)

    bloqueio_ate = datetime.now(timezone.utc) + timedelta(days=dias_bloqueio)
    baseline = metas_atuais or {}
    baseline_json = json.dumps(baseline, ensure_ascii=False)

    async with async_session() as sessao:
        usuario = await _garantir_usuario(sessao, alvo.id)
        usuario.bloqueio_promocao_ate = bloqueio_ate
        usuario.metas_baseline_json = baseline_json

        resumo_cargo = f"{cargo_de} → {cargo_para}"
        punicao = Punicao(
            discord_id=alvo.id,
            id_fivem=usuario.id_fivem,
            tipo="REBAIXAMENTO",
            cargo_id=role_destino.id,
            cargo_nome=resumo_cargo[:80],
            motivo=motivo[:1500],
            executor_id=executor.id,
            ativa=True,
            origem="MANUAL",
        )
        sessao.add(punicao)
        await sessao.flush()

        historico = HistoricoPunicao(
            discord_id=alvo.id,
            tipo="REBAIXAMENTO",
            cargo_de=cargo_de,
            cargo_para=cargo_para,
            motivo=motivo[:500],
            executado_por=executor.id,
            dias_bloqueio=dias_bloqueio,
            bloqueio_ate=bloqueio_ate,
            punicao_id=punicao.id,
            modo=modo,
            criado_em=agora(),
        )
        sessao.add(historico)
        await sessao.commit()

    partes = [
        f"Rebaixado de **{cargo_de}** para **{cargo_para}**.",
        f"Bloqueio de promoção: **{dias_bloqueio} dias**.",
    ]
    if nomes_removidos:
        partes.append(
            "Removidos: " + ", ".join(f"**{n}**" for n in nomes_removidos) + "."
        )
    if canceladas:
        partes.append(f"Solicitações de promoção canceladas: **{canceladas}**.")
    return True, " ".join(partes)


async def publicar_log_rebaixamento(
    guild: discord.Guild,
    *,
    alvo: discord.Member,
    executor: discord.Member,
    cargo_de: str,
    cargo_para: str,
    motivo: str,
    dias_bloqueio: int,
    detalhe: str,
) -> None:
    """Publica nos canais CANAL_REBAIXADOS e LOG_REBAIXAMENTOS."""
    from src.config import CANAIS

    texto = (
        f"## ⬇️ Rebaixamento\n"
        f"**Membro:** {alvo.mention} (`{alvo.id}`)\n"
        f"**De:** {cargo_de}\n"
        f"**Para:** {cargo_para}\n"
        f"**Motivo:** {motivo}\n"
        f"**Bloqueio promoção:** {dias_bloqueio} dias\n"
        f"**Executor:** {executor.mention}\n"
        f"-# {detalhe}"
    )
    for chave in ("CANAL_REBAIXADOS", "LOG_REBAIXAMENTOS"):
        canal_id = CANAIS.get(chave) or 0
        if not canal_id:
            continue
        canal = guild.get_channel(int(canal_id))
        if canal is None or not hasattr(canal, "send"):
            continue
        try:
            await canal.send(texto)
        except (discord.Forbidden, discord.HTTPException) as erro:
            registrador.warning("Falha ao logar rebaixamento em %s: %s", chave, erro)


async def listar_rebaixamentos_ativos(
    discord_id: int | None = None,
    *,
    limite: int = 25,
) -> list[Punicao]:
    """Punições ativas do tipo REBAIXAMENTO (opcionalmente de um membro)."""
    async with async_session() as sessao:
        consulta = select(Punicao).where(
            Punicao.tipo == "REBAIXAMENTO",
            Punicao.ativa.is_(True),
        )
        if discord_id is not None:
            consulta = consulta.where(Punicao.discord_id == discord_id)
        consulta = consulta.order_by(Punicao.criada_em.desc()).limit(limite)
        resultado = await sessao.execute(consulta)
        return list(resultado.scalars().all())


async def obter_historico_do_rebaixamento(
    punicao_id: int,
) -> HistoricoPunicao | None:
    """Histórico ligado ao id da punição de rebaixamento."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(HistoricoPunicao).where(
                HistoricoPunicao.punicao_id == punicao_id,
                HistoricoPunicao.tipo == "REBAIXAMENTO",
            )
        )
        return resultado.scalar_one_or_none()


async def revogar_rebaixamento(
    *,
    guild: discord.Guild,
    alvo: discord.Member,
    executor: discord.Member,
    punicao_id: int,
    motivo: str,
) -> tuple[bool, str]:
    """
    Revoga um rebaixamento ativo.

    - Marca a Punicao como inativa
    - Restaura o cargo de origem (cargo_de do histórico), se possível
    - Remove o cargo de destino se ainda for o cargo hierárquico do membro
    - Limpa bloqueio de promoção e baseline de metas
    - Atualiza tag do nick
    """
    if not membro_pode_revogar_rebaixamento(executor):
        return False, "Sem permissão para revogar rebaixamento."

    motivo_limpo = (motivo or "").strip() or "Revogação de rebaixamento"
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Punicao).where(
                Punicao.id == punicao_id,
                Punicao.discord_id == alvo.id,
                Punicao.tipo == "REBAIXAMENTO",
                Punicao.ativa.is_(True),
            )
        )
        punicao = resultado.scalar_one_or_none()
        if punicao is None:
            return False, "Rebaixamento ativo não encontrado para este membro."

        resultado_hist = await sessao.execute(
            select(HistoricoPunicao).where(
                HistoricoPunicao.punicao_id == punicao_id,
                HistoricoPunicao.tipo == "REBAIXAMENTO",
            )
        )
        historico = resultado_hist.scalar_one_or_none()
        cargo_de = historico.cargo_de if historico else None
        cargo_para = historico.cargo_para if historico else None
        if not cargo_de or not cargo_para:
            # Fallback pelo texto cargo_nome "A → B"
            bruto = (punicao.cargo_nome or "").split("→")
            if len(bruto) == 2 and not cargo_de:
                cargo_de = bruto[0].strip()
            if len(bruto) == 2 and not cargo_para:
                cargo_para = bruto[1].strip()

        if not cargo_de:
            return False, "Não foi possível identificar o cargo de origem para restaurar."

        role_origem = _resolver_role(guild, cargo_de)
        if role_origem is None:
            return False, f"Cargo de origem `{cargo_de}` não encontrado no servidor."

        roles_remover: list[discord.Role] = []
        if cargo_para:
            role_destino = _resolver_role(guild, cargo_para)
            if role_destino is not None and role_destino in alvo.roles:
                roles_remover.append(role_destino)

        motivo_discord = f"Revogação de rebaixamento por {executor} — {motivo_limpo[:200]}"
        try:
            if roles_remover:
                await alvo.remove_roles(*roles_remover, reason=motivo_discord)
            if role_origem not in alvo.roles:
                await alvo.add_roles(role_origem, reason=motivo_discord)
        except discord.Forbidden:
            return False, (
                "Sem permissão para alterar cargos deste membro "
                "(hierarquia do bot abaixo do cargo?)."
            )
        except discord.HTTPException as erro:
            return False, f"Erro Discord ao restaurar cargos: {erro}"

        try:
            nick_atual = alvo.nick or alvo.display_name or alvo.name
            novo_nick = aplicar_prefixo(nick_atual, cargo_de)
            if novo_nick and novo_nick != alvo.nick:
                await alvo.edit(nick=novo_nick[:32], reason=motivo_discord)
        except (discord.Forbidden, discord.HTTPException) as erro:
            registrador.warning(
                "Revogação OK mas falha no nick de %s: %s", alvo.id, erro
            )

        agora_utc = datetime.now(timezone.utc)
        punicao.ativa = False
        punicao.removida_em = agora_utc
        punicao.removida_por = executor.id
        punicao.motivo_remocao = motivo_limpo[:500]

        usuario = await _garantir_usuario(sessao, alvo.id)
        usuario.bloqueio_promocao_ate = None
        usuario.metas_baseline_json = None
        await sessao.commit()

    detalhe = (
        f"Rebaixamento `#{punicao_id}` revogado. "
        f"Cargo restaurado: **{cargo_de}**"
        + (f" (removido **{cargo_para}**)." if cargo_para else ".")
        + " Bloqueio de promoção e baseline de metas limpos."
    )
    return True, detalhe


async def publicar_log_revogacao_rebaixamento(
    guild: discord.Guild,
    *,
    alvo: discord.Member,
    executor: discord.Member,
    punicao_id: int,
    motivo: str,
    detalhe: str,
) -> None:
    """Log da revogação nos mesmos canais de rebaixamento."""
    from src.config import CANAIS

    texto = (
        f"## ♻️ Revogação de rebaixamento\n"
        f"**Membro:** {alvo.mention} (`{alvo.id}`)\n"
        f"**Punição:** `#{punicao_id}`\n"
        f"**Motivo:** {motivo}\n"
        f"**Executor:** {executor.mention}\n"
        f"-# {detalhe}"
    )
    for chave in ("CANAL_REBAIXADOS", "LOG_REBAIXAMENTOS"):
        canal_id = CANAIS.get(chave) or 0
        if not canal_id:
            continue
        canal = guild.get_channel(int(canal_id))
        if canal is None or not hasattr(canal, "send"):
            continue
        try:
            await canal.send(texto)
        except (discord.Forbidden, discord.HTTPException) as erro:
            registrador.warning(
                "Falha ao logar revogação de rebaixamento em %s: %s", chave, erro
            )
