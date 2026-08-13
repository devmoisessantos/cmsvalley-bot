# src/backup/banco_discord_backup.py
"""
Cofre do banco de dados no canal LOG_BACKUP do Discord.

Fluxo simples (sem API externa):
  - Exportar → gera JSON e posta no LOG_BACKUP (com anexo)
  - Listar / baixar → lê mensagens marcadas nesse canal
  - Verificar → compara hash local com o último backup do canal
  - Importar → JSON editado (VS Code) via anexo → INSERT só do que falta

Modal do Discord NÃO aceita arquivo. Por isso o upload é:
  - comando /backup banco-importar com anexo, ou
  - botão do painel que espera a próxima mensagem sua com o .json
"""

from __future__ import annotations

import io
import json
import re
from datetime import (
    datetime,
    timezone,
)
from typing import Any

import discord

from src.backup.api_db_sync import (
    exportar_snapshot_banco,
    restaurar_faltantes_no_banco,
)
from src.config import (
    CANAIS,
    MESES_ABREV,
)
from src.utils.error_handling import LoggingViewMixin
from src.utils.formatacao import (
    agora_brasilia,
    para_horario_brasilia,
)
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_sucesso,
)

MARCADOR_BACKUP_DB = "🗄️ DB_BACKUP"
PADRAO_HASH = re.compile(r"hash=`([a-f0-9]{16,64})`", re.IGNORECASE)


def _canal_log_backup(guilda: discord.Guild) -> discord.TextChannel | None:
    canal_id = CANAIS.get("LOG_BACKUP")
    if not canal_id:
        return None
    canal = guilda.get_channel(int(canal_id))
    if isinstance(canal, discord.TextChannel):
        return canal
    return None


def _contar_linhas(snapshot: dict[str, Any]) -> tuple[int, int]:
    tabelas = snapshot.get("tabelas") or {}
    quantidade_tabelas = len(tabelas)
    quantidade_linhas = 0
    for bloco in tabelas.values():
        if isinstance(bloco, dict):
            quantidade_linhas += len(bloco.get("linhas") or [])
    return quantidade_tabelas, quantidade_linhas


def _formatar_momento_backup(data_hora: datetime | None = None) -> str:
    """Ex.: 13 Ago de 2026 - 03:22:16 (Brasília)."""
    local = para_horario_brasilia(data_hora) if data_hora else agora_brasilia()
    if local is None:
        local = agora_brasilia()
    nome_mes = MESES_ABREV.get(local.month, "—")
    return f"{local.day} {nome_mes} de {local.year} - {local.strftime('%H:%M:%S')}"


def _formatar_numero_linhas(quantidade: int) -> str:
    """Ex.: 3526 → 3.526"""
    return f"{int(quantidade):,}".replace(",", ".")


def _montar_conteudo_parseavel(
    snapshot: dict[str, Any],
    *,
    autor: str | None = None,
) -> str:
    """
    Texto curto na mensagem (não é o card visual).
    Serve para o bot achar o hash no history sem abrir o LayoutView.
    """
    hash_completo = snapshot.get("hash_conteudo") or ""
    linhas = [
        MARCADOR_BACKUP_DB,
        f"hash=`{hash_completo}`",
    ]
    if autor:
        linhas.append(f"por=`{autor}`")
    return "\n".join(linhas)


def _montar_card_backup_db(
    guilda: discord.Guild,
    snapshot: dict[str, Any],
    *,
    autor: str | None = None,
) -> discord.ui.LayoutView:
    """
    Card Components V2 no canal LOG_BACKUP.

    Layout pedido:
      # 🗄️ CMS Valley - Backup DB + thumbnail
      hash / tabelas / linhas / em / por
      separador
      verificação + status
    """
    hash_completo = snapshot.get("hash_conteudo") or "—"
    hash_curto = hash_completo[:16] if hash_completo != "—" else "—"
    quantidade_tabelas, quantidade_linhas = _contar_linhas(snapshot)

    momento_iso = snapshot.get("atualizado_em")
    momento_dt: datetime | None = None
    if momento_iso:
        try:
            momento_dt = datetime.fromisoformat(str(momento_iso).replace("Z", "+00:00"))
        except ValueError:
            momento_dt = None
    texto_momento = _formatar_momento_backup(momento_dt)

    autor_texto = autor or "Sistema"
    verificacao = (
        "automática"
        if "automátic" in autor_texto.lower() or "sistema" in autor_texto.lower()
        else "manual"
    )

    corpo = (
        f"> `🔐` * **Hash:** ||{hash_completo}||\n"
        f"> `✂️` * **Hash curto:** `{hash_curto}`\n"
        f"> `📊` * **Tabelas:** `{quantidade_tabelas}`\n"
        f"> `📄` * **Linhas:** `{_formatar_numero_linhas(quantidade_linhas)}`\n"
        f"> `🕐` * **Em:** `{texto_momento}`\n"
        f"> `👤` * **Por:** *{autor_texto}*"
    )
    rodape = f"* **Verificação:** `{verificacao}`\n* **Status:** ||✅ Concluído||"

    url_icone = None
    if guilda.icon is not None:
        url_icone = guilda.icon.url

    componentes: list = []
    if url_icone:
        componentes.append(
            discord.ui.Section(
                "# 🗄️ CMS Valley - Backup DB",
                corpo,
                accessory=discord.ui.Thumbnail(url_icone),
            )
        )
    else:
        componentes.append(
            discord.ui.TextDisplay(f"# 🗄️ CMS Valley - Backup DB\n{corpo}")
        )

    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes.append(discord.ui.TextDisplay(rodape))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            *componentes,
            accent_color=discord.Color.dark_teal(),
        )
    )
    return view


def _extrair_hash_do_conteudo(conteudo: str | None) -> str | None:
    if not conteudo:
        return None
    encontrado = PADRAO_HASH.search(conteudo)
    if encontrado:
        return encontrado.group(1)
    return None


def _anexo_json_da_mensagem(mensagem: discord.Message) -> discord.Attachment | None:
    for anexo in mensagem.attachments:
        nome = (anexo.filename or "").lower()
        if nome.endswith(".json"):
            return anexo
        if "json" in (anexo.content_type or ""):
            return anexo
    return None


async def ler_snapshot_do_anexo(anexo: discord.Attachment) -> dict[str, Any]:
    dados_brutos = await anexo.read()
    texto = dados_brutos.decode("utf-8")
    snapshot = json.loads(texto)
    if not isinstance(snapshot, dict) or "tabelas" not in snapshot:
        raise ValueError(
            "JSON inválido: precisa ter a chave `tabelas` (export do bot)."
        )
    return snapshot


async def exportar_banco_para_canal(
    guilda: discord.Guild,
    *,
    autor: str | None = None,
    forcar: bool = False,
) -> dict[str, Any]:
    """
    Gera snapshot e envia ao LOG_BACKUP.

    Se forcar=False e o hash for igual ao último do canal, não posta de novo.
    """
    canal = _canal_log_backup(guilda)
    if canal is None:
        return {
            "enviado": False,
            "motivo": "Canal LOG_BACKUP não encontrado no config/guilda.",
        }

    snapshot = await exportar_snapshot_banco()
    hash_local = snapshot.get("hash_conteudo") or ""

    if not forcar:
        ultimo = await obter_ultimo_backup_mensagem(canal)
        if ultimo is not None:
            hash_canal = _extrair_hash_do_conteudo(ultimo.content)
            if hash_canal and hash_canal == hash_local:
                return {
                    "enviado": False,
                    "motivo": "sem alteração (hash igual ao último no canal)",
                    "hash": hash_local,
                    "mensagem_id": ultimo.id,
                }

    quantidade_tabelas, quantidade_linhas = _contar_linhas(snapshot)
    carimbo = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    nome_arquivo = f"db_backup_{carimbo}.json"
    conteudo_json = json.dumps(snapshot, ensure_ascii=False, indent=2)
    arquivo = discord.File(
        fp=io.BytesIO(conteudo_json.encode("utf-8")),
        filename=nome_arquivo,
    )

    # Components V2 não permite content + view na mesma mensagem.
    # 1ª = card visual | 2ª = marcador/hash + anexo JSON
    view_card = _montar_card_backup_db(guilda, snapshot, autor=autor)
    mensagem_card = await canal.send(view=view_card)

    texto_parseavel = _montar_conteudo_parseavel(snapshot, autor=autor)
    mensagem_arquivo = await canal.send(
        content=texto_parseavel,
        file=arquivo,
    )

    return {
        "enviado": True,
        "motivo": "backup postado no LOG_BACKUP",
        "hash": hash_local,
        "tabelas": quantidade_tabelas,
        "linhas": quantidade_linhas,
        "mensagem_id": mensagem_arquivo.id,
        "mensagem_card_id": mensagem_card.id,
        "arquivo": nome_arquivo,
        "canal_id": canal.id,
    }


async def obter_ultimo_backup_mensagem(
    canal: discord.TextChannel,
) -> discord.Message | None:
    async for mensagem in canal.history(limit=50):
        if MARCADOR_BACKUP_DB in (mensagem.content or ""):
            if _anexo_json_da_mensagem(mensagem) is not None:
                return mensagem
    return None


async def listar_backups_do_canal(
    guilda: discord.Guild,
    *,
    limite: int = 10,
) -> list[dict[str, Any]]:
    canal = _canal_log_backup(guilda)
    if canal is None:
        return []

    encontrados: list[dict[str, Any]] = []
    async for mensagem in canal.history(limit=80):
        if MARCADOR_BACKUP_DB not in (mensagem.content or ""):
            continue
        anexo = _anexo_json_da_mensagem(mensagem)
        if anexo is None:
            continue
        encontrados.append(
            {
                "mensagem_id": mensagem.id,
                "criado_em": mensagem.created_at.isoformat(),
                "hash": _extrair_hash_do_conteudo(mensagem.content),
                "arquivo": anexo.filename,
                "url": anexo.url,
                "tamanho": anexo.size,
                "autor": str(mensagem.author),
                "jump_url": mensagem.jump_url,
            }
        )
        if len(encontrados) >= limite:
            break
    return encontrados


async def verificar_banco_vs_canal(guilda: discord.Guild) -> dict[str, Any]:
    snapshot = await exportar_snapshot_banco()
    hash_local = snapshot.get("hash_conteudo") or ""
    quantidade_tabelas, quantidade_linhas = _contar_linhas(snapshot)

    canal = _canal_log_backup(guilda)
    if canal is None:
        return {
            "ok": False,
            "motivo": "LOG_BACKUP ausente",
            "hash_local": hash_local,
            "tabelas": quantidade_tabelas,
            "linhas": quantidade_linhas,
        }

    ultimo = await obter_ultimo_backup_mensagem(canal)
    if ultimo is None:
        return {
            "ok": True,
            "igual": False,
            "motivo": "nenhum backup no canal ainda",
            "hash_local": hash_local,
            "hash_canal": None,
            "tabelas": quantidade_tabelas,
            "linhas": quantidade_linhas,
        }

    hash_canal = _extrair_hash_do_conteudo(ultimo.content)
    return {
        "ok": True,
        "igual": bool(hash_canal and hash_canal == hash_local),
        "motivo": "comparado com o último do canal",
        "hash_local": hash_local,
        "hash_canal": hash_canal,
        "tabelas": quantidade_tabelas,
        "linhas": quantidade_linhas,
        "mensagem_id": ultimo.id,
        "jump_url": ultimo.jump_url,
    }


async def importar_snapshot_aditivo(snapshot: dict[str, Any]) -> dict[str, int]:
    """Aplica o JSON no Postgres: só cria linhas que faltam."""
    return await restaurar_faltantes_no_banco(snapshot)


# ---------------------------------------------------------------------------
# Painel ephemeral (Components V2)
# ---------------------------------------------------------------------------


class PainelBancoBackupView(LoggingViewMixin, discord.ui.LayoutView):
    """Painel admin: exportar, listar, verificar, importar por anexo."""

    def __init__(self, bot: discord.Client, membro_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.membro_id = membro_id

        linha_principal = discord.ui.ActionRow()
        botao_exportar = discord.ui.Button(
            label="Exportar para LOG_BACKUP",
            style=discord.ButtonStyle.success,
            emoji="📤",
            custom_id="backup_db:exportar",
        )
        botao_exportar.callback = self._ao_exportar
        botao_verificar = discord.ui.Button(
            label="Verificar",
            style=discord.ButtonStyle.primary,
            emoji="🔍",
            custom_id="backup_db:verificar",
        )
        botao_verificar.callback = self._ao_verificar
        linha_principal.add_item(botao_exportar)
        linha_principal.add_item(botao_verificar)

        linha_secundaria = discord.ui.ActionRow()
        botao_listar = discord.ui.Button(
            label="Listar no canal",
            style=discord.ButtonStyle.secondary,
            emoji="📋",
            custom_id="backup_db:listar",
        )
        botao_listar.callback = self._ao_listar
        botao_importar = discord.ui.Button(
            label="Importar JSON (anexo)",
            style=discord.ButtonStyle.danger,
            emoji="📥",
            custom_id="backup_db:importar",
        )
        botao_importar.callback = self._ao_importar
        linha_secundaria.add_item(botao_listar)
        linha_secundaria.add_item(botao_importar)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# 🗄️ Painel — Backup do banco\n"
                    "Cofre = canal **LOG_BACKUP** (arquivo JSON anexado).\n"
                    "Edição: baixe o JSON → VS Code → envie de volta (só adiciona linhas)."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(
                    "## Ações\n"
                    "• **Exportar** — posta snapshot atual no canal (se hash mudou).\n"
                    "• **Verificar** — compara banco local × último do canal.\n"
                    "• **Listar** — últimos backups com link de download.\n"
                    "• **Importar** — envie o `.json` na próxima mensagem (90s).\n"
                    "• Atalho slash: `/backup banco-importar` com anexo."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha_principal,
                linha_secundaria,
                accent_color=discord.Color.dark_teal(),
            )
        )

    def _autor_ok(self, interacao: discord.Interaction) -> bool:
        return interacao.user.id == self.membro_id

    async def _ao_exportar(self, interacao: discord.Interaction):
        if not self._autor_ok(interacao):
            await responder_erro(
                interacao,
                titulo="Não é o seu painel",
                linhas=["Só quem abriu o painel pode usar os botões."],
            )
            return
        await interacao.response.defer(ephemeral=True)
        guilda = interacao.guild
        if guilda is None:
            await responder_erro(
                interacao,
                titulo="Só no servidor",
                linhas=["Use o painel dentro do Discord do hospital."],
            )
            return
        try:
            resultado = await exportar_banco_para_canal(
                guilda,
                autor=str(interacao.user),
                forcar=False,
            )
            if resultado.get("enviado"):
                await responder_sucesso(
                    interacao,
                    titulo="Backup enviado ao canal",
                    linhas=[
                        f"Arquivo: `{resultado.get('arquivo')}`",
                        f"Hash: `{str(resultado.get('hash') or '')[:16]}…`",
                        f"Tabelas: **{resultado.get('tabelas')}** · "
                        f"Linhas: **{resultado.get('linhas')}**",
                        f"Canal: <#{resultado.get('canal_id')}>",
                    ],
                    delay=30,
                )
            else:
                await responder_aviso(
                    interacao,
                    titulo="Nada postado",
                    linhas=[
                        resultado.get("motivo") or "sem detalhes",
                        f"Hash atual: `{str(resultado.get('hash') or '')[:16]}…`",
                    ],
                    delay=20,
                )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Falha ao exportar",
                linhas=[str(erro)[:300]],
            )

    async def _ao_verificar(self, interacao: discord.Interaction):
        if not self._autor_ok(interacao):
            await responder_erro(
                interacao,
                titulo="Não é o seu painel",
                linhas=["Só quem abriu o painel pode usar os botões."],
            )
            return
        await interacao.response.defer(ephemeral=True)
        guilda = interacao.guild
        if guilda is None:
            await responder_erro(
                interacao,
                titulo="Só no servidor",
                linhas=["Use dentro do servidor."],
            )
            return
        try:
            resultado = await verificar_banco_vs_canal(guilda)
            if (
                not resultado.get("ok")
                and resultado.get("motivo") == "LOG_BACKUP ausente"
            ):
                await responder_erro(
                    interacao,
                    titulo="Canal ausente",
                    linhas=["Configure CANAIS['LOG_BACKUP'] no config."],
                )
                return
            if resultado.get("igual"):
                await responder_sucesso(
                    interacao,
                    titulo="Banco igual ao último backup",
                    linhas=[
                        f"Hash: `{str(resultado.get('hash_local') or '')[:20]}…`",
                        f"Tabelas: **{resultado.get('tabelas')}** · "
                        f"Linhas: **{resultado.get('linhas')}**",
                        f"Mensagem: {resultado.get('jump_url') or '—'}",
                    ],
                    delay=25,
                )
            else:
                await responder_aviso(
                    interacao,
                    titulo="Há diferença (ou ainda não há backup)",
                    linhas=[
                        resultado.get("motivo") or "",
                        f"Hash local: `{str(resultado.get('hash_local') or '')[:20]}…`",
                        f"Hash canal: `{str(resultado.get('hash_canal') or 'nenhum')[:20]}…`",
                        f"Tabelas: **{resultado.get('tabelas')}** · "
                        f"Linhas: **{resultado.get('linhas')}**",
                        "Use **Exportar** para atualizar o canal, ou **Importar** se o canal estiver mais completo.",
                    ],
                    delay=35,
                )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Falha ao verificar",
                linhas=[str(erro)[:300]],
            )

    async def _ao_listar(self, interacao: discord.Interaction):
        if not self._autor_ok(interacao):
            await responder_erro(
                interacao,
                titulo="Não é o seu painel",
                linhas=["Só quem abriu o painel pode usar os botões."],
            )
            return
        await interacao.response.defer(ephemeral=True)
        guilda = interacao.guild
        if guilda is None:
            await responder_erro(
                interacao,
                titulo="Só no servidor",
                linhas=["Use dentro do servidor."],
            )
            return
        try:
            lista = await listar_backups_do_canal(guilda, limite=8)
            if not lista:
                await responder_aviso(
                    interacao,
                    titulo="Nenhum backup no canal",
                    linhas=[
                        "Ainda não há mensagem com o marcador "
                        f"`{MARCADOR_BACKUP_DB}` e anexo `.json`.",
                        "Use **Exportar** para criar o primeiro.",
                    ],
                    delay=20,
                )
                return
            linhas = []
            for indice, item in enumerate(lista, start=1):
                hash_curto = (item.get("hash") or "?")[:12]
                linhas.append(
                    f"**{indice}.** `{item.get('arquivo')}` · "
                    f"hash `{hash_curto}…`\n"
                    f"→ [abrir mensagem]({item.get('jump_url')}) · "
                    f"[download]({item.get('url')})"
                )
            await responder_sucesso(
                interacao,
                titulo="Backups no LOG_BACKUP",
                linhas=linhas,
                delay=60,
                com_marcador=False,
            )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Falha ao listar",
                linhas=[str(erro)[:300]],
            )

    async def _ao_importar(self, interacao: discord.Interaction):
        if not self._autor_ok(interacao):
            await responder_erro(
                interacao,
                titulo="Não é o seu painel",
                linhas=["Só quem abriu o painel pode usar os botões."],
            )
            return

        await responder_aviso(
            interacao,
            titulo="Envie o arquivo JSON",
            linhas=[
                "Nas **próximas 90 segundos**, mande neste canal (ou em DM comigo) "
                "uma mensagem **só com o anexo** `.json` do backup.",
                "O bot lê o arquivo e **só adiciona** linhas que faltam no banco "
                "(nunca apaga o que já existe).",
                "Atalho: `/backup banco-importar` + anexo.",
            ],
            delay=90,
        )

        def _filtro(mensagem: discord.Message) -> bool:
            if mensagem.author.id != interacao.user.id:
                return False
            return _anexo_json_da_mensagem(mensagem) is not None

        try:
            mensagem_arquivo = await self.bot.wait_for(
                "message",
                check=_filtro,
                timeout=90,
            )
        except TimeoutError:
            await responder_aviso(
                interacao,
                titulo="Tempo esgotado",
                linhas=[
                    "Nenhum `.json` recebido em 90s.",
                    "Tente de novo ou use `/backup banco-importar`.",
                ],
                delay=15,
            )
            return

        anexo = _anexo_json_da_mensagem(mensagem_arquivo)
        if anexo is None:
            await responder_erro(
                interacao,
                titulo="Anexo inválido",
                linhas=["Não achei um arquivo `.json` na mensagem."],
            )
            return

        try:
            snapshot = await ler_snapshot_do_anexo(anexo)
            estatisticas = await importar_snapshot_aditivo(snapshot)
            await responder_sucesso(
                interacao,
                titulo="Importação concluída (só faltantes)",
                linhas=[
                    f"Arquivo: `{anexo.filename}`",
                    f"Linhas inseridas: **{estatisticas.get('linhas_inseridas', 0)}**",
                    f"Já existiam: **{estatisticas.get('linhas_ja_existiam', 0)}**",
                    f"Tabelas tocadas: **{estatisticas.get('tabelas_tocadas', 0)}**",
                    f"Erros: **{estatisticas.get('erros', 0)}**",
                ],
                delay=40,
            )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Falha ao importar",
                linhas=[str(erro)[:400]],
            )
