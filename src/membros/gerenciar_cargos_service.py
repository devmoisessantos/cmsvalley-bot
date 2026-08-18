"""Serviço de adicionar/remover cargos com escopos e anti-abuso.

Trava importante (anti-abuso da GATE):
  Só se pode CONCEDER cargo a quem já passou pelo recrutamento.
  Visitante / whitelist-only / sem registro em ``usuarios`` → bloqueado.

Ao conceder um cargo que tem entrada em PREFIXOS_NICKNAME, o apelido
do membro é atualizado com o prefixo correspondente.
"""

from __future__ import annotations

import discord
from sqlalchemy import select

from src.config import (
    CANAIS,
    CARGOS,
    CARGOS_HIERARQUIA,
    ESCOPOS_GERENCIAMENTO,
    JANELA_TEMPO_SUSPEITA_SEGUNDOS,
    PREFIXOS_NICKNAME,
)
from src.database.conexao import async_session
from src.database.models import Usuario
from src.utils.log_container import LogContainerView
from src.utils.logger import log_mudanca_cargo
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_info,
    responder_sucesso,
)
from src.utils.nickname import aplicar_prefixo
from src.utils.rate_limiter import registrar_remocao

# Cargos que só existem depois do recrutamento formal.
# Ter qualquer um deles (ou status APROVADO no banco) libera o gerenciamento.
CARGOS_PROVA_RECRUTAMENTO = {
    "HP S・Valley",
    "Aprovado",
    "🔰・Enfermeiro (a)",
    "🚑・Paramédico",
    *CARGOS_HIERARQUIA,
}


def _cargo_permitido_para_executor(executor: discord.Member, nome_cargo: str) -> bool:
    """Verifica se o executor pode gerenciar um cargo específico."""
    escopos_do_executor = determinar_escopos(executor)

    for escopo in escopos_do_executor:
        cargos_que_pode_gerenciar = listar_cargos_do_escopo(escopo, executor)
        if nome_cargo in cargos_que_pode_gerenciar:
            return True

    return False


def determinar_escopos(membro: discord.Member) -> list[str]:
    """Retorna as chaves de ESCOPOS_GERENCIAMENTO que esse membro pode usar."""
    escopos_do_membro: list[str] = []

    for chave_escopo, config_escopo in ESCOPOS_GERENCIAMENTO.items():
        ids_autorizados_no_escopo = {
            CARGOS[nome_do_cargo]
            for nome_do_cargo in config_escopo["cargos_autorizados"]
            if nome_do_cargo in CARGOS
        }

        membro_tem_acesso = any(
            cargo_do_membro.id in ids_autorizados_no_escopo
            for cargo_do_membro in membro.roles
        )

        if membro_tem_acesso:
            escopos_do_membro.append(chave_escopo)

    return escopos_do_membro


def listar_cargos_do_escopo(escopo: str, membro_executor: discord.Member) -> list[str]:
    """Cargos que o executor pode gerenciar dentro de um escopo."""
    config_escopo = ESCOPOS_GERENCIAMENTO[escopo]
    cargos_gerenciaveis = config_escopo["cargos_gerenciaveis"]

    if cargos_gerenciaveis is None:
        todos_os_cargos: list[str] = []
        for outro_escopo in ESCOPOS_GERENCIAMENTO.values():
            candidatos = outro_escopo["cargos_gerenciaveis"]
            if candidatos is None:
                continue
            if isinstance(candidatos, dict):
                for lista_de_cargos in candidatos.values():
                    todos_os_cargos.extend(lista_de_cargos)
            elif isinstance(candidatos, list):
                todos_os_cargos.extend(candidatos)
        return todos_os_cargos

    if isinstance(cargos_gerenciaveis, list):
        return cargos_gerenciaveis

    if isinstance(cargos_gerenciaveis, dict):
        ids_dos_cargos_do_membro = {cargo.id for cargo in membro_executor.roles}

        for (
            nome_do_cargo_gerenciador,
            lista_de_gerenciaveis,
        ) in cargos_gerenciaveis.items():
            id_do_cargo_gerenciador = CARGOS.get(nome_do_cargo_gerenciador)
            if (
                id_do_cargo_gerenciador
                and id_do_cargo_gerenciador in ids_dos_cargos_do_membro
            ):
                return lista_de_gerenciaveis

        return []

    return []


# ══════════════════════════════════════════════════════════════════════════
# Trava: só quem passou pelo recrutamento recebe cargo gerenciável
# ══════════════════════════════════════════════════════════════════════════


def _tem_cargo_de_recrutamento(membro: discord.Member) -> bool:
    """True se o membro já tem Enfermeiro, Paramédico, HPS, Aprovado ou hierarquia."""
    ids_do_membro = {cargo.id for cargo in membro.roles}
    for nome_cargo in CARGOS_PROVA_RECRUTAMENTO:
        id_cargo = CARGOS.get(nome_cargo)
        if id_cargo and id_cargo in ids_do_membro:
            return True
    return False


async def _buscar_usuario(discord_id: int) -> Usuario | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Usuario).where(Usuario.discord_id == discord_id)
        )
        return resultado.scalar_one_or_none()


async def candidato_elegivel_para_receber_cargo(
    candidato: discord.Member,
) -> tuple[bool, str]:
    """Decide se o membro pode RECEBER um cargo via gerenciar_cargos.

    Bloqueia:
      - sem registro em ``usuarios`` (recém-chegado / nunca sincronizado)
      - só fez whitelist (Visitante) sem recrutamento
      - status VISITANTE e sem cargo mínimo de recrutamento

    Libera:
      - status APROVADO / ja_foi_aprovado no banco
      - já possui Enfermeiro, Paramédico, HPS, Aprovado ou hierarquia
    """
    if _tem_cargo_de_recrutamento(candidato):
        return True, ""

    usuario = await _buscar_usuario(candidato.id)

    if usuario is None:
        return (
            False,
            f"❌ {candidato.mention} **não possui registro** na tabela de usuários.\n"
            "Só é permitido conceder cargos a quem **passou pelo recrutamento** "
            "(Enfermeiro ou Paramédico no mínimo).\n"
            "Visitantes / whitelist-only não podem receber cargos por aqui.",
        )

    if usuario.status == "APROVADO" or usuario.ja_foi_aprovado:
        return True, ""

    # Ainda estudante ou visitante no banco, sem cargo mínimo
    return (
        False,
        f"❌ {candidato.mention} ainda **não concluiu o recrutamento** "
        f"(status no banco: `{usuario.status}`).\n"
        "É obrigatório ter sido aprovado e possuir ao menos o cargo de "
        "**Enfermeiro** ou **Paramédico** antes de receber outros cargos.\n"
        "Membros só com whitelist (**Visitantes**) não podem ser promovidos por este "
        "painel.",
    )


async def _aplicar_prefixo_no_apelido(
    candidato: discord.Member,
    nome_cargo: str,
) -> str | None:
    """Atualiza o nick com PREFIXOS_NICKNAME do cargo. Retorna o novo nick ou None."""
    if nome_cargo not in PREFIXOS_NICKNAME:
        return None

    nome_atual = candidato.nick or candidato.display_name or candidato.name
    novo_nick = aplicar_prefixo(nome_atual, nome_cargo)

    if novo_nick == nome_atual:
        return None

    try:
        await candidato.edit(
            nick=novo_nick,
            reason=f"Prefixo automático ao receber cargo {nome_cargo}",
        )
        return novo_nick
    except discord.Forbidden:
        return None
    except discord.HTTPException:
        return None


# ══════════════════════════════════════════════════════════════════════════
# Adicionar / Remover
# ══════════════════════════════════════════════════════════════════════════


async def adicionar_cargo(
    interaction: discord.Interaction,
    candidato: discord.Member,
    nome_cargo: str,
):
    """Adiciona um cargo ao candidato (com trava de recrutamento + prefixo)."""
    guild = interaction.guild
    executor = interaction.user

    if not _cargo_permitido_para_executor(executor, nome_cargo):
        await responder_erro(
            interaction,
            titulo="Sem permissão",
            linhas=[
                "Você não tem permissão para gerenciar esse cargo.",
            ],
        )
        return

    if candidato.id == executor.id:
        await responder_erro(
            interaction,
            titulo="Ação não permitida",
            linhas=[
                "Você não pode atribuir cargos a si mesmo.",
            ],
        )
        return

    # ── Trava anti-abuso: só recrutados ──────────────────────────────────
    elegivel, mensagem_bloqueio = await candidato_elegivel_para_receber_cargo(candidato)
    if not elegivel:
        await responder_info(
            interaction,
            titulo="Ação bloqueada",
            linhas=[
                mensagem_bloqueio,
            ],
        )
        return

    cargo = guild.get_role(CARGOS[nome_cargo])
    if cargo is None:
        await responder_erro(
            interaction,
            titulo="Não encontrado",
            linhas=[
                f"Cargo `{nome_cargo}` não encontrado no servidor.",
            ],
        )
        return

    if cargo in candidato.roles:
        await responder_erro(
            interaction,
            titulo="Cargo já aplicado",
            linhas=[
                f"{candidato.mention} já possui o cargo {cargo.mention}.",
            ],
        )
        return

    await candidato.add_roles(
        cargo,
        reason=f"Adicionado via gerenciar_cargos por {executor}",
    )
    await log_mudanca_cargo(
        guild,
        candidato=candidato,
        executor=executor,
        cargos_adicionados=[cargo.mention],
    )

    # Prefixo no apelido (se o cargo tiver mapeamento)
    novo_nick = await _aplicar_prefixo_no_apelido(candidato, nome_cargo)
    extra_nick = f"\n📝 Apelido atualizado para: `{novo_nick}`" if novo_nick else ""

    await responder_sucesso(
        interaction,
        titulo="Cargo adicionado",
        linhas=[
            f"Cargo {cargo.mention} adicionado a {candidato.mention}.{extra_nick}",
        ],
    )


async def remover_cargo(
    interaction: discord.Interaction,
    candidato: discord.Member,
    nome_cargo: str,
):
    """Remove um cargo do candidato (com anti-abuso de remoções rápidas)."""
    guild = interaction.guild
    executor = interaction.user

    if not _cargo_permitido_para_executor(executor, nome_cargo):
        await responder_erro(
            interaction,
            titulo="Sem permissão",
            linhas=[
                "Você não tem permissão para gerenciar esse cargo.",
            ],
        )
        return

    if candidato.id == executor.id:
        await responder_erro(
            interaction,
            titulo="Ação não permitida",
            linhas=[
                "Você não pode remover cargos de si mesmo.",
            ],
        )
        return

    cargo = guild.get_role(CARGOS[nome_cargo])
    if cargo is None:
        await responder_erro(
            interaction,
            titulo="Não encontrado",
            linhas=[
                f"Cargo `{nome_cargo}` não encontrado no servidor.",
            ],
        )
        return

    if cargo not in candidato.roles:
        await responder_erro(
            interaction,
            titulo="Cargo ausente no membro",
            linhas=[
                f"{candidato.mention} não possui o cargo {cargo.mention}.",
            ],
        )
        return

    await candidato.remove_roles(
        cargo,
        reason=f"Removido via gerenciar_cargos por {executor}",
    )
    await log_mudanca_cargo(
        guild,
        candidato=candidato,
        executor=executor,
        cargos_removidos=[cargo.mention],
    )

    remocoes_recentes = registrar_remocao(
        executor.id, candidato.id, cargo.id, nome_cargo
    )

    if remocoes_recentes is not None:
        await _reverter_remocoes_suspeitas(guild, executor, remocoes_recentes)
        await responder_aviso(
            interaction,
            titulo="Muitas ações em pouco tempo",
            linhas=[
                "Você está fazendo isso rápido demais. As remoções recentes foram "
                "revertidas e essa atividade foi registrada no log de cargos.",
            ],
        )
        return

    await responder_sucesso(
        interaction,
        titulo="Cargo removido",
        linhas=[
            f"Cargo {cargo.mention} removido de {candidato.mention}.",
        ],
    )


async def _reverter_remocoes_suspeitas(
    guild: discord.Guild,
    executor: discord.Member,
    remocoes: list[tuple[float, int, int, str]],
):
    """Devolve cargos removidos em rajada e registra no log."""
    linhas_do_relatorio: list[str] = []

    for _, id_do_candidato, id_do_cargo, _nome_do_cargo in remocoes:
        candidato = guild.get_member(id_do_candidato)
        cargo = guild.get_role(id_do_cargo)

        if candidato is None or cargo is None:
            continue

        if cargo not in candidato.roles:
            await candidato.add_roles(
                cargo,
                reason="Reversão automática - atividade suspeita detectada",
            )

        linhas_do_relatorio.append(f"- {candidato.mention}: {cargo.mention} restaurado")

    canal_de_log = guild.get_channel(CANAIS["LOG_CARGOS"])
    if canal_de_log is None:
        return

    texto_do_relatorio = (
        f"- **Executor:** {executor.mention} (`{executor.id}`)\n"
        f"- **Motivo:** {len(remocoes)} remoções de cargo em menos de "
        f"{JANELA_TEMPO_SUSPEITA_SEGUNDOS}s\n\n" + "\n".join(linhas_do_relatorio)
    )

    view_de_log = LogContainerView(
        titulo="⚠️ Atividade Suspeita Detectada",
        linhas=texto_do_relatorio,
        guild=guild,
        cor=discord.Color.orange(),
        avatar_url=executor.display_avatar.url,
    )

    await canal_de_log.send(view=view_de_log)
