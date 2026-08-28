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
    acao: discord.AuditLogAction | list[discord.AuditLogAction] | None = None,
    *,
    alvo_id: int | None = None,
    limite: int = 12,
    segundos_de_tolerancia: int = 30,
) -> discord.abc.User | None:
    """
    Tenta descobrir quem fez a ação pelo Audit Log recente.

    ``acao`` pode ser uma ação ou lista (tenta na ordem).
    """
    if acao is None:
        lista_acoes: list[discord.AuditLogAction | None] = [None]
    elif isinstance(acao, list):
        lista_acoes = list(acao)
    else:
        lista_acoes = [acao]

    try:
        for acao_atual in lista_acoes:
            kwargs = {"limit": limite}
            if acao_atual is not None:
                kwargs["action"] = acao_atual
            async for entrada in guilda.audit_logs(**kwargs):
                if entrada.created_at is None:
                    continue
                idade = (
                    datetime.now(timezone.utc) - entrada.created_at
                ).total_seconds()
                if idade > segundos_de_tolerancia:
                    continue
                if alvo_id is not None:
                    alvo = entrada.target
                    id_do_alvo = getattr(alvo, "id", None)
                    if id_do_alvo is not None and int(id_do_alvo) != int(alvo_id):
                        continue
                if entrada.user is not None:
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


async def buscar_executor_alteracao_canal(
    guilda: discord.Guild,
    canal_id: int,
    *,
    limite: int = 15,
    segundos_de_tolerancia: int = 45,
) -> discord.abc.User | None:
    """
    Descobre quem alterou um canal (nome, tópico, permissões, etc.).

    Permissões de canal no Discord geram entradas ``overwrite_create``,
    ``overwrite_update`` ou ``overwrite_delete``. Nessas entradas o
    *target* é o cargo/membro da overwrite — o canal fica em ``extra``.
    Por isso a busca genérica por alvo_id=canal falhava.
    """
    acoes = [
        discord.AuditLogAction.channel_update,
        discord.AuditLogAction.overwrite_update,
        discord.AuditLogAction.overwrite_create,
        discord.AuditLogAction.overwrite_delete,
    ]
    try:
        for acao in acoes:
            async for entrada in guilda.audit_logs(limit=limite, action=acao):
                if entrada.created_at is None:
                    continue
                idade = (
                    datetime.now(timezone.utc) - entrada.created_at
                ).total_seconds()
                if idade > segundos_de_tolerancia:
                    continue

                # channel_update: target é o canal
                id_alvo = getattr(entrada.target, "id", None)
                if id_alvo is not None and int(id_alvo) == int(canal_id):
                    if entrada.user is not None:
                        return entrada.user

                # overwrite_*: canal vem em extra / extra.channel
                extra = getattr(entrada, "extra", None)
                if extra is not None:
                    id_canal_extra = getattr(extra, "id", None)
                    if id_canal_extra is None:
                        canal_extra = getattr(extra, "channel", None)
                        id_canal_extra = getattr(canal_extra, "id", None)
                    if id_canal_extra is not None and int(id_canal_extra) == int(
                        canal_id
                    ):
                        if entrada.user is not None:
                            return entrada.user
    except discord.Forbidden:
        registrador.warning(
            "Sem permissão 'Ver registro de auditoria' ao buscar "
            "responsável do canal %s",
            canal_id,
        )
    except discord.HTTPException as erro_http:
        registrador.debug("Audit log (canal) indisponível: %s", erro_http)
    return None


async def baixar_arquivo_de_url(
    _sessao_http: discord.Client | None,
    url: str,
    nome_do_arquivo: str,
) -> discord.File | None:
    """
    Baixa uma URL (ex.: avatar no CDN) e devolve um discord.File.

    Usa User-Agent e timeout, no mesmo espírito do download da chamada.
    Prefira ``arquivo_de_asset_discord`` quando ainda tiver o Asset em mão.
    """
    if not url:
        return None
    try:
        import aiohttp

        cabecalhos = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; CMSValleyBot/1.0; +https://discord.com)"
            ),
            "Accept": "image/*,*/*;q=0.8",
        }
        async with aiohttp.ClientSession(headers=cabecalhos) as sessao:
            async with sessao.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resposta:
                if resposta.status != 200:
                    registrador.warning(
                        "Download de mídia status=%s url=%s",
                        resposta.status,
                        url[:120],
                    )
                    return None
                dados = await resposta.read()
                if not dados:
                    return None
                tipo = (resposta.headers.get("Content-Type") or "").lower()
                nome = nome_do_arquivo
                if "gif" in tipo and not nome.lower().endswith(".gif"):
                    nome = nome.rsplit(".", 1)[0] + ".gif"
                elif "png" in tipo and not nome.lower().endswith(".png"):
                    nome = nome.rsplit(".", 1)[0] + ".png"
                elif ("jpeg" in tipo or "jpg" in tipo) and not nome.lower().endswith(
                    (".jpg", ".jpeg")
                ):
                    nome = nome.rsplit(".", 1)[0] + ".jpg"
                elif "webp" in tipo and not nome.lower().endswith(".webp"):
                    nome = nome.rsplit(".", 1)[0] + ".webp"
                return discord.File(BytesIO(dados), filename=nome)
    except Exception as erro:
        registrador.warning("Falha ao baixar mídia %s: %s", url[:120], erro)
        return None


async def arquivo_de_asset_discord(
    asset: discord.Asset | None,
    nome_base: str = "avatar",
) -> discord.File | None:
    """
    Lê um Asset do discord.py (avatar, guild_avatar, etc.) via API oficial.

    Mais confiável que baixar a URL do CDN com aiohttp.
    """
    if asset is None:
        return None
    try:
        # Garante resolução boa sem quebrar o asset
        try:
            asset_lido = asset.with_size(1024)
        except Exception:
            asset_lido = asset
        dados = await asset_lido.read()
        if not dados:
            return None
        # Detecta GIF pelo header
        if dados[:6] in (b"GIF87a", b"GIF89a"):
            nome = f"{nome_base}.gif"
        elif dados[:8] == b"\x89PNG\r\n\x1a\n":
            nome = f"{nome_base}.png"
        elif dados[:2] == b"\xff\xd8":
            nome = f"{nome_base}.jpg"
        else:
            nome = f"{nome_base}.png"
        return discord.File(BytesIO(dados), filename=nome)
    except Exception as erro:
        registrador.warning("Falha ao ler Asset Discord: %s", erro)
        # Fallback: tenta a URL do asset
        try:
            url = str(asset.url)
        except Exception:
            return None
        return await baixar_arquivo_de_url(None, url, f"{nome_base}.png")


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
        titulo="🔧 Alteração de Cargo(s)",
        linhas=partes,
        cor=COR_INFO,
        url_do_avatar=candidato.display_avatar.url,
    )


async def log_decisao(
    guilda: discord.Guild,
    canal_ou_chave=None,
    *,
    titulo: str = "Decisão",
    linhas: str | list[str] | None = None,
    chave_do_canal: str = "LOG_AUDITORIA_ADMIN",
    cor: discord.Color = COR_INFO,
    url_do_avatar: str | None = None,
    # Assinatura antiga (recrutamento, etc.) — mantida para não quebrar
    candidato: discord.abc.User | None = None,
    executor: discord.abc.User | None = None,
    cargo: str | None = None,
    extra: str = "",
):
    """
    Log de decisão administrativa.

    Duas formas de uso:

    1) Nova (genérica)::

        await log_decisao(guilda, titulo="...", linhas=[...], chave_do_canal="LOG_...")

    2) Antiga (recrutamento / aprovação)::

        await log_decisao(
            guilda,
            CANAIS["LOG_APROVACOES"],  # id do canal
            titulo="✅ Candidato Aprovado",
            candidato=membro,
            executor=staff,
            cargo="@Enfermeiro",
            extra="Nota: 90%",
            cor=discord.Color.green(),
        )
    """
    # Resolve canal: id numérico antigo OU chave em CANAIS
    id_do_canal = 0
    if isinstance(canal_ou_chave, int):
        id_do_canal = int(canal_ou_chave)
    elif isinstance(canal_ou_chave, str) and canal_ou_chave.isdigit():
        id_do_canal = int(canal_ou_chave)
    elif isinstance(canal_ou_chave, str) and canal_ou_chave in CANAIS:
        chave_do_canal = canal_ou_chave
    # senão mantém chave_do_canal padrão / informada

    # Monta linhas no formato antigo quando não vierem prontas
    if linhas is None:
        partes: list[str] = []
        if candidato is not None:
            partes.append(f"- **Membro:** {candidato.mention} (`{candidato.id}`)")
        if cargo:
            partes.append(f"- **Cargo:** {cargo}")
        if executor is not None:
            partes.append(f"- **Por:** {executor.mention}")
        if extra:
            partes.append(f"- {extra}")
        linhas = partes if partes else ["_sem detalhes_"]
        if url_do_avatar is None and candidato is not None:
            avatar = getattr(candidato, "display_avatar", None)
            if avatar is not None:
                url_do_avatar = avatar.url

    # Envio por ID direto (assinatura antiga com CANAIS["LOG_..."])
    if id_do_canal > 0:
        canal = guilda.get_channel(id_do_canal)
        if canal is None:
            try:
                canal = await guilda.fetch_channel(id_do_canal)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                registrador.warning("log_decisao: canal %s não encontrado", id_do_canal)
                return
        if isinstance(linhas, list):
            texto = "\n".join(linhas)
        else:
            texto = linhas
        view_do_log = LogContainerView(
            titulo=titulo,
            linhas=texto,
            guild=guilda,
            cor=cor,
            avatar_url=url_do_avatar,
        )
        try:
            await canal.send(view=view_do_log)
        except (discord.Forbidden, discord.HTTPException) as erro:
            registrador.warning("log_decisao falhou no canal %s: %s", id_do_canal, erro)
        return

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
    "buscar_executor_alteracao_canal",
    "baixar_arquivo_de_url",
    "arquivo_de_asset_discord",
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
