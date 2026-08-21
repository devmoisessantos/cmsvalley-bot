# src/utils/logger.py
"""
Funções de log de auditoria (cargos, decisões e logs genéricos do servidor).

Todas usam LogContainerView (Components V2).
O envio genérico passa por ``publicar_log_auditoria`` para um único padrão visual.
"""

from __future__ import annotations

import logging
from datetime import (
    datetime,
    timezone,
)
from io import BytesIO

import discord

from src.config import CANAIS
from src.utils.log_container import LogContainerView
from src.utils.mensagens import (
    COR_AVISO,
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
)

registrador = logging.getLogger(__name__)

# Membros cujo log de cargo acabou de ser publicado por um serviço do bot.
# O listener de on_member_update consulta isto para não duplicar o card.
_cargos_publicados_pelo_bot: dict[int, float] = {}


def obter_id_do_canal_de_log(chave_do_canal: str) -> int:
    """
    Lê o ID do canal em CANAIS.

    Retorna 0 quando a chave não existe ou está desligada (valor 0).
    """
    valor = CANAIS.get(chave_do_canal) or 0
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


async def publicar_log_auditoria(
    guilda: discord.Guild,
    chave_do_canal: str,
    *,
    titulo: str,
    linhas: str | list[str],
    cor: discord.Color = COR_INFO,
    url_do_avatar: str | None = None,
    urls_de_midia: list[str] | None = None,
    arquivos: list[discord.File] | None = None,
    abrir_topico_para_anexos: bool = False,
    nome_do_topico: str | None = None,
) -> discord.Message | None:
    """
    Publica um card de log no canal configurado em CANAIS[chave_do_canal].

    - Se o ID for 0 ou o canal não existir, não faz nada (retorna None).
    - ``linhas`` pode ser string pronta ou lista de itens (juntos com quebra).
    - ``arquivos`` / ``abrir_topico_para_anexos``: anexa arquivos na mensagem
      ou, se pedido, cria um tópico na mensagem e envia os anexos lá.

    Este é o caminho único para logs de auditoria do servidor.
    """
    id_do_canal = obter_id_do_canal_de_log(chave_do_canal)
    if id_do_canal <= 0:
        registrador.debug(
            "Log %s desligado (CANAIS['%s'] = 0)",
            chave_do_canal,
            chave_do_canal,
        )
        return None

    canal = guilda.get_channel(id_do_canal)
    if canal is None or not isinstance(canal, (discord.TextChannel, discord.Thread)):
        registrador.debug(
            "Canal de log %s (%s) não encontrado",
            chave_do_canal,
            id_do_canal,
        )
        return None

    if isinstance(linhas, list):
        texto_das_linhas = "\n".join(linhas)
    else:
        texto_das_linhas = linhas

    view_do_log = LogContainerView(
        titulo=titulo,
        linhas=texto_das_linhas,
        guild=guilda,
        cor=cor,
        avatar_url=url_do_avatar,
        midia_urls=urls_de_midia,
    )

    try:
        # Anexos grandes / imagem antiga vão no tópico para não poluir o card
        if abrir_topico_para_anexos and arquivos:
            mensagem = await canal.send(view=view_do_log)
            nome = (nome_do_topico or "anexos-do-log")[:100]
            try:
                topico = await mensagem.create_thread(name=nome)
                await topico.send(files=arquivos)
            except (discord.Forbidden, discord.HTTPException) as erro_topico:
                registrador.warning(
                    "Não foi possível abrir tópico no log %s: %s",
                    chave_do_canal,
                    erro_topico,
                )
                # Fallback: tenta anexar na própria mensagem (reenvia)
                try:
                    await canal.send(view=view_do_log, files=arquivos)
                except discord.HTTPException:
                    pass
            return mensagem

        if arquivos:
            mensagem = await canal.send(view=view_do_log, files=arquivos)
        else:
            mensagem = await canal.send(view=view_do_log)
        return mensagem
    except discord.Forbidden:
        registrador.warning(
            "Sem permissão para enviar log em %s (%s)",
            chave_do_canal,
            id_do_canal,
        )
    except discord.HTTPException as erro_http:
        registrador.warning(
            "Falha HTTP ao enviar log em %s: %s",
            chave_do_canal,
            erro_http,
        )
    return None


async def buscar_executor_no_audit_log(
    guilda: discord.Guild,
    acao: discord.AuditLogAction,
    *,
    alvo_id: int | None = None,
    limite: int = 6,
    segundos_de_tolerancia: int = 20,
) -> discord.abc.User | None:
    """
    Tenta descobrir quem fez a ação pelo Audit Log recente.

    Retorna o usuário executor ou None se não houver permissão / entrada.
    """
    try:
        async for entrada in guilda.audit_logs(limit=limite, action=acao):
            if entrada.created_at is None:
                continue
            idade = (datetime.now(timezone.utc) - entrada.created_at).total_seconds()
            if idade > segundos_de_tolerancia:
                continue
            if alvo_id is not None:
                alvo = entrada.target
                id_do_alvo = getattr(alvo, "id", None)
                if id_do_alvo is not None and int(id_do_alvo) != int(alvo_id):
                    continue
            return entrada.user
    except discord.Forbidden:
        registrador.debug("Sem permissão view_audit_log em %s", guilda.id)
    except discord.HTTPException as erro_http:
        registrador.debug("Audit log indisponível: %s", erro_http)
    return None


async def baixar_arquivo_de_url(
    sessao_http: discord.Client | None,
    url: str,
    nome_do_arquivo: str,
) -> discord.File | None:
    """Baixa uma URL (ex.: avatar antigo) e devolve um discord.File."""
    try:
        if sessao_http is not None and hasattr(sessao_http, "http"):
            # Usa o connector do bot quando possível
            import aiohttp

            async with aiohttp.ClientSession() as sessao:
                async with sessao.get(url) as resposta:
                    if resposta.status != 200:
                        return None
                    dados = await resposta.read()
                    return discord.File(
                        BytesIO(dados),
                        filename=nome_do_arquivo,
                    )
        import aiohttp

        async with aiohttp.ClientSession() as sessao:
            async with sessao.get(url) as resposta:
                if resposta.status != 200:
                    return None
                dados = await resposta.read()
                return discord.File(BytesIO(dados), filename=nome_do_arquivo)
    except Exception as erro:
        registrador.debug("Falha ao baixar mídia %s: %s", url, erro)
        return None


async def log_cargo(
    guilda: discord.Guild,
    canal_id: int,
    *,
    candidato: discord.Member,
    executor: discord.abc.User,
    acao: str,
    cargo: str,
    extra: str = "",
):
    """
    Log simples de uma ação de cargo em um canal qualquer.

    Preferível usar log_mudanca_cargo ou log_decisao quando fizer sentido.
    """
    canal = guilda.get_channel(canal_id)
    if canal is None:
        return

    momento_atual = int(datetime.now(timezone.utc).timestamp())

    linhas = (
        f"**{acao}**\n"
        f"- **Membro:** {candidato.mention} (`{candidato.id}`)\n"
        f"- **Cargo:** {cargo}\n"
        f"- **Executor:** {executor.mention}\n"
        f"- **Data:** <t:{momento_atual}:F>"
    )
    if extra:
        linhas += f"\n- {extra}"

    view_do_log = LogContainerView(
        titulo="📋 Ação de Cargo",
        linhas=linhas,
        guild=guilda,
        cor=COR_INFO,
        avatar_url=candidato.display_avatar.url,
    )
    await canal.send(view=view_do_log)


async def log_mudanca_cargo(
    guilda: discord.Guild,
    *,
    candidato: discord.Member,
    executor: discord.abc.User,
    cargos_adicionados: list[str] | None = None,
    cargos_removidos: list[str] | None = None,
):
    """
    Auditoria de alteração de cargos (bot ou manual).

    Envia no canal LOG_CARGOS. Também usada pelos serviços do bot quando
    eles mesmos aplicam cargos, para manter o mesmo visual.
    """
    partes = [f"- **Membro:** {candidato.mention} (`{candidato.id}`)"]

    if cargos_adicionados:
        lista_adicionados = ", ".join(cargos_adicionados)
        partes.append(f"- **Adicionados:** {lista_adicionados}")

    if cargos_removidos:
        lista_removidos = ", ".join(cargos_removidos)
        partes.append(f"- **Removidos:** {lista_removidos}")

    partes.append(f"- **Alterado por:** {executor.mention}")

    import time

    _cargos_publicados_pelo_bot[int(candidato.id)] = time.monotonic()
    if len(_cargos_publicados_pelo_bot) > 400:
        _cargos_publicados_pelo_bot.clear()

    await publicar_log_auditoria(
        guilda,
        "LOG_CARGOS",
        titulo="🔍 🔧 Alteração de Cargo(s)",
        linhas=partes,
        cor=COR_INFO,
        url_do_avatar=candidato.display_avatar.url,
    )


async def log_decisao(
    guilda: discord.Guild,
    *,
    titulo: str,
    linhas: str | list[str],
    chave_do_canal: str = "LOG_AUDITORIA_ADMIN",
    cor: discord.Color = COR_INFO,
    url_do_avatar: str | None = None,
):
    """Log genérico de decisão administrativa no canal indicado."""
    await publicar_log_auditoria(
        guilda,
        chave_do_canal,
        titulo=titulo,
        linhas=linhas,
        cor=cor,
        url_do_avatar=url_do_avatar,
    )


# Cores exportadas para os listeners de auditoria
__all__ = [
    "publicar_log_auditoria",
    "buscar_executor_no_audit_log",
    "baixar_arquivo_de_url",
    "obter_id_do_canal_de_log",
    "log_cargo",
    "log_mudanca_cargo",
    "log_decisao",
    "COR_INFO",
    "COR_SUCESSO",
    "COR_AVISO",
    "COR_ERRO",
]


def cargo_ja_foi_logado_pelo_bot(discord_id: int, segundos: float = 4.0) -> bool:
    """True se um serviço do bot já publicou log de cargo deste membro agora há pouco."""
    import time

    momento = _cargos_publicados_pelo_bot.get(int(discord_id))
    if momento is None:
        return False
    return (time.monotonic() - momento) < segundos
