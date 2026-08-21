# src/utils/logger.py
"""
Funções de log de auditoria (cargos, decisões e logs genéricos do servidor).

Todas usam LogContainerView (Components V2).
O envio genérico passa por ``publicar_log_auditoria`` — caminho único.
"""

from __future__ import annotations

import asyncio
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
_cargos_publicados_pelo_bot: dict[int, float] = {}


def obter_id_do_canal_de_log(chave_do_canal: str) -> int:
    """Lê o ID do canal em CANAIS. 0 = desligado."""
    valor = CANAIS.get(chave_do_canal) or 0
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


async def resolver_canal_de_log(
    guilda: discord.Guild,
    chave_do_canal: str,
    *,
    cliente: discord.Client | None = None,
) -> discord.abc.Messageable | None:
    """
    Resolve o canal de log de forma resistente a cache frio.

    Ordem:
    1. guilda.get_channel
    2. cliente.get_channel
    3. guilda.fetch_channel / cliente.fetch_channel
    """
    id_do_canal = obter_id_do_canal_de_log(chave_do_canal)
    if id_do_canal <= 0:
        registrador.warning(
            "Log %s desligado: CANAIS['%s'] = 0 — configure o ID no config.py",
            chave_do_canal,
            chave_do_canal,
        )
        return None

    canal = guilda.get_channel(id_do_canal)
    if canal is not None:
        return canal

    if cliente is not None:
        canal = cliente.get_channel(id_do_canal)
        if canal is not None:
            return canal

    # Cache pode não ter o canal (restart, canal novo, falta de intent parcial)
    try:
        if cliente is not None:
            canal = await cliente.fetch_channel(id_do_canal)
        else:
            canal = await guilda.fetch_channel(id_do_canal)
        return canal
    except discord.NotFound:
        registrador.error(
            "Canal de log %s não existe (ID %s). Confira o config.py.",
            chave_do_canal,
            id_do_canal,
        )
    except discord.Forbidden:
        registrador.error(
            "Sem permissão para ver/enviar no canal de log %s (ID %s). "
            "O bot precisa Ver canal + Enviar mensagens nesse canal.",
            chave_do_canal,
            id_do_canal,
        )
    except discord.HTTPException as erro_http:
        registrador.error(
            "Falha ao buscar canal de log %s (ID %s): %s",
            chave_do_canal,
            id_do_canal,
            erro_http,
        )
    return None


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
    cliente: discord.Client | None = None,
    url_do_link: str | None = None,
    rotulo_do_link: str = "Abrir mensagem",
    blocos_extra: list[str] | None = None,
) -> discord.Message | None:
    """
    Publica um card de log no canal CANAIS[chave_do_canal].

    Use sempre este helper nos listeners de auditoria.
    Falhas são registradas em WARNING/ERROR (não só debug).
    """
    canal = await resolver_canal_de_log(
        guilda,
        chave_do_canal,
        cliente=cliente,
    )
    if canal is None:
        return None

    if not hasattr(canal, "send"):
        registrador.error(
            "Canal de log %s não aceita send (tipo %s)",
            chave_do_canal,
            type(canal).__name__,
        )
        return None

    if isinstance(linhas, list):
        texto_das_linhas = "\n".join(linhas)
    else:
        texto_das_linhas = linhas

    # Discord limita o tamanho do TextDisplay — corta com aviso
    if len(texto_das_linhas) > 3800:
        texto_das_linhas = texto_das_linhas[:3800] + "\n…"

    view_do_log = LogContainerView(
        titulo=titulo,
        linhas=texto_das_linhas,
        guild=guilda,
        cor=cor,
        avatar_url=url_do_avatar,
        midia_urls=urls_de_midia,
        link_url=url_do_link,
        link_label=rotulo_do_link,
        blocos_extra=blocos_extra,
    )

    try:
        if abrir_topico_para_anexos and arquivos:
            mensagem = await canal.send(view=view_do_log)
            nome = (nome_do_topico or "anexos-do-log")[:100]
            try:
                topico = await mensagem.create_thread(name=nome)
                await topico.send(files=arquivos)
                # Mesmo fluxo de chamadas/advertências: espera 2s e arquiva
                await asyncio.sleep(2)
                try:
                    await topico.edit(
                        archived=True,
                        locked=True,
                        reason="Arquivar tópico de anexo do log",
                    )
                except discord.HTTPException as erro_fechar:
                    registrador.warning(
                        "Falha ao arquivar tópico do log %s: %s",
                        chave_do_canal,
                        erro_fechar,
                    )
                    try:
                        await topico.edit(
                            archived=True,
                            reason="Arquivar tópico de anexo do log",
                        )
                    except discord.HTTPException as erro_fallback:
                        registrador.warning(
                            "Fallback archived falhou no log %s: %s",
                            chave_do_canal,
                            erro_fallback,
                        )
            except (discord.Forbidden, discord.HTTPException) as erro_topico:
                registrador.warning(
                    "Não foi possível abrir tópico no log %s: %s",
                    chave_do_canal,
                    erro_topico,
                )
                try:
                    await canal.send(files=arquivos)
                except discord.HTTPException:
                    pass
            return mensagem

        if arquivos:
            mensagem = await canal.send(view=view_do_log, files=arquivos)
        else:
            mensagem = await canal.send(view=view_do_log)
        registrador.info(
            "Log publicado em %s (canal %s): %s",
            chave_do_canal,
            getattr(canal, "id", "?"),
            titulo,
        )
        return mensagem
    except discord.Forbidden:
        registrador.error(
            "Sem permissão para ENVIAR no canal de log %s (ID %s). "
            "Marque Ver canal + Enviar mensagens + Incorporar links para o bot.",
            chave_do_canal,
            getattr(canal, "id", "?"),
        )
    except discord.HTTPException as erro_http:
        registrador.error(
            "HTTP ao enviar log %s: %s | titulo=%s",
            chave_do_canal,
            erro_http,
            titulo,
        )
    except Exception:
        registrador.exception(
            "Erro inesperado ao publicar log %s (titulo=%s)",
            chave_do_canal,
            titulo,
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
    """Tenta descobrir quem fez a ação pelo Audit Log recente."""
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
        registrador.warning(
            "Sem permissão 'Ver registro de auditoria' em %s — "
            "campos 'quem fez' podem ficar vazios",
            guilda.id,
        )
    except discord.HTTPException as erro_http:
        registrador.debug("Audit log indisponível: %s", erro_http)
    return None


async def baixar_arquivo_de_url(
    _sessao_http: discord.Client | None,
    url: str,
    nome_do_arquivo: str,
) -> discord.File | None:
    """Baixa uma URL (ex.: avatar antigo) e devolve um discord.File."""
    try:
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
    """Log simples de uma ação de cargo em um canal qualquer."""
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
        titulo="🔍 📋 Ação de Cargo",
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
    """Auditoria de alteração de cargos (bot ou manual) → LOG_CARGOS."""
    import time

    _cargos_publicados_pelo_bot[int(candidato.id)] = time.monotonic()
    if len(_cargos_publicados_pelo_bot) > 400:
        _cargos_publicados_pelo_bot.clear()

    partes = [f"**Membro:** {candidato.mention} (`{candidato.id}`)"]
    if cargos_adicionados:
        partes.append(f"**Adicionados:** {', '.join(cargos_adicionados)}")
    if cargos_removidos:
        partes.append(f"**Removidos:** {', '.join(cargos_removidos)}")
    partes.append(f"**Alterado por:** {executor.mention}")

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
    """Log genérico de decisão administrativa."""
    await publicar_log_auditoria(
        guilda,
        chave_do_canal,
        titulo=titulo,
        linhas=linhas,
        cor=cor,
        url_do_avatar=url_do_avatar,
    )


def cargo_ja_foi_logado_pelo_bot(discord_id: int, segundos: float = 4.0) -> bool:
    """True se um serviço do bot já publicou log de cargo deste membro há pouco."""
    import time

    momento = _cargos_publicados_pelo_bot.get(int(discord_id))
    if momento is None:
        return False
    return (time.monotonic() - momento) < segundos


__all__ = [
    "publicar_log_auditoria",
    "resolver_canal_de_log",
    "buscar_executor_no_audit_log",
    "baixar_arquivo_de_url",
    "obter_id_do_canal_de_log",
    "log_cargo",
    "log_mudanca_cargo",
    "log_decisao",
    "cargo_ja_foi_logado_pelo_bot",
    "COR_INFO",
    "COR_SUCESSO",
    "COR_AVISO",
    "COR_ERRO",
]
