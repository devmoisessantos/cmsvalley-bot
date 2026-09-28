# src/backup/banco_discord_backup.py
"""
Cofre do banco de dados no canal LOG_BACKUP do Discord.

Fluxo simples (sem API externa):
  - Exportar → gera snapshot, compacta em .zip e posta no LOG_BACKUP
  - Listar / baixar → lê mensagens marcadas nesse canal
  - Verificar → compara hash local com o último backup do canal
  - Importar → .zip (ou .json legado) via anexo → INSERT só do que falta

Modal do Discord NÃO aceita arquivo. Por isso o upload é:
  - comando /backup banco-importar com anexo, ou
  - botão do painel que espera a próxima mensagem sua com o .zip
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import zipfile
from datetime import (
    datetime,
    timezone,
)
from typing import Any

import discord

from src.backup.sincronizacao_api_service import (
    exportar_snapshot_banco,
    restaurar_faltantes_no_banco,
)
from src.config import (
    BACKUP_DIR,
    CANAIS,
    MAX_BACKUPS_PER_GUILD,
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

registrador = logging.getLogger(__name__)

MARCADOR_BACKUP_DB = "🗄️ DB_BACKUP"
PADRAO_HASH = re.compile(r"hash=`([a-f0-9]{16,64})`", re.IGNORECASE)
# Nome do anexo: db_backup_<hash16>_<timestamp>.zip (legado .json ainda aceito)
PADRAO_HASH_NO_ARQUIVO = re.compile(
    r"db_backup_([a-f0-9]{16})_",
    re.IGNORECASE,
)
NOME_JSON_DENTRO_DO_ZIP = "snapshot.json"
# Limite seguro do Discord para anexo (bots normais: 25 MB).
# Fica abaixo de propósito para não falhar no limite exato.
LIMITE_ANEXO_DISCORD_BYTES = 24 * 1024 * 1024


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


def _formatar_tamanho_arquivo(bytes_tamanho: int) -> str:
    """Ex.: 2097152 → 2 MB"""
    if bytes_tamanho < 1024:
        return f"{bytes_tamanho} B"
    if bytes_tamanho < 1024 * 1024:
        return f"{bytes_tamanho / 1024:.1f} KB".replace(".0 KB", " KB")
    megas = bytes_tamanho / (1024 * 1024)
    if megas < 10:
        return f"{megas:.1f} MB".replace(".0 MB", " MB")
    return f"{int(round(megas))} MB"


def _montar_card_backup_db(
    guilda: discord.Guild,
    snapshot: dict[str, Any],
    *,
    autor: str | None = None,
    nome_arquivo: str,
    tamanho_bytes: int,
) -> discord.ui.LayoutView:
    """
    Card Components V2 único no LOG_BACKUP (+ anexo JSON na mesma mensagem).

    Hash curto fica no nome do arquivo para o bot comparar sem content.
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
        f"> `👤` * **Por:** *{autor_texto}*\n\n"
        f"## 📦 Arquivo\n"
        f"* `{nome_arquivo}`\n"
        f"* **Tamanho:** `{_formatar_tamanho_arquivo(tamanho_bytes)}`"
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


def _extrair_hash_do_nome_arquivo(nome: str | None) -> str | None:
    if not nome:
        return None
    encontrado = PADRAO_HASH_NO_ARQUIVO.search(nome)
    if encontrado:
        return encontrado.group(1).lower()
    return None


def _extrair_hash_da_mensagem(mensagem: discord.Message) -> str | None:
    """Hash no content (legado) ou no nome do anexo JSON."""
    hash_content = _extrair_hash_do_conteudo(mensagem.content)
    if hash_content:
        return hash_content.lower()
    anexo = _anexo_json_da_mensagem(mensagem)
    if anexo is not None:
        return _extrair_hash_do_nome_arquivo(anexo.filename)
    return None


def _anexo_backup_da_mensagem(
    mensagem: discord.Message,
) -> discord.Attachment | None:
    """
    Acha o anexo de backup do banco na mensagem.

    Aceita .zip (formato atual) e .json (legado).
    """
    for anexo in mensagem.attachments:
        nome = (anexo.filename or "").lower()
        if nome.endswith(".zip") or nome.endswith(".json"):
            return anexo
        tipo = (anexo.content_type or "").lower()
        if "zip" in tipo or "json" in tipo:
            return anexo
    return None


# Nome antigo ainda usado em alguns pontos do painel; aponta para a mesma lógica.
def _anexo_json_da_mensagem(mensagem: discord.Message) -> discord.Attachment | None:
    return _anexo_backup_da_mensagem(mensagem)


def _decodificar_snapshot_dos_bytes(
    dados_brutos: bytes,
    nome_arquivo: str,
) -> dict[str, Any]:
    """
    Lê bytes de um .zip ou .json e devolve o dicionário do snapshot.

    No .zip espera um único JSON (snapshot.json ou o primeiro .json encontrado).
    """
    nome = (nome_arquivo or "").lower()
    if nome.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(dados_brutos), "r") as arquivo_zip:
            nomes_dentro = arquivo_zip.namelist()
            nome_json = None
            if NOME_JSON_DENTRO_DO_ZIP in nomes_dentro:
                nome_json = NOME_JSON_DENTRO_DO_ZIP
            else:
                for candidato in nomes_dentro:
                    if candidato.lower().endswith(".json"):
                        nome_json = candidato
                        break
            if nome_json is None:
                raise ValueError(
                    "ZIP inválido: não achei nenhum .json dentro do arquivo."
                )
            texto = arquivo_zip.read(nome_json).decode("utf-8")
    else:
        texto = dados_brutos.decode("utf-8")

    snapshot = json.loads(texto)
    if not isinstance(snapshot, dict) or "tabelas" not in snapshot:
        raise ValueError(
            "Snapshot inválido: precisa ter a chave `tabelas` (export do bot)."
        )
    return snapshot


async def ler_snapshot_do_anexo(anexo: discord.Attachment) -> dict[str, Any]:
    """
    Converte um anexo (.zip ou .json legado) em retrato de banco para importação.

    Exige a chave `tabelas`, estrutura mínima de uma exportação do bot.
    """
    dados_brutos = await anexo.read()
    return _decodificar_snapshot_dos_bytes(
        dados_brutos,
        anexo.filename or "",
    )


def _pasta_cofre_local() -> str:
    """
    Pasta no disco do bot onde todo ZIP de backup do banco é gravado.

    Esta cópia existe para não depender só do Discord: se o upload falhar,
    o arquivo ainda fica no servidor (Fadehost) para importar depois.
    """
    caminho = os.path.join(BACKUP_DIR, "database")
    os.makedirs(caminho, exist_ok=True)
    return caminho


def _limpar_zips_locais_antigos() -> None:
    """Mantém só os N ZIPs mais recentes na pasta local do cofre."""
    pasta = _pasta_cofre_local()
    nomes = sorted(
        (
            nome
            for nome in os.listdir(pasta)
            if nome.startswith("db_backup_") and nome.endswith(".zip")
        ),
        reverse=True,
    )
    limite = max(1, int(MAX_BACKUPS_PER_GUILD or 10))
    for nome_antigo in nomes[limite:]:
        try:
            os.remove(os.path.join(pasta, nome_antigo))
        except OSError as erro_ao_apagar:
            registrador.warning(
                "[backup-db] não apaguei ZIP local antigo %s: %s",
                nome_antigo,
                erro_ao_apagar,
            )


def _gravar_zip_local(nome_arquivo: str, bytes_zip: bytes) -> str | None:
    """
    Grava o ZIP no disco e devolve o caminho absoluto.

    Retorna None se o disco falhar — o fluxo ainda tenta o Discord.
    """
    try:
        caminho = os.path.join(_pasta_cofre_local(), nome_arquivo)
        with open(caminho, "wb") as arquivo_local:
            arquivo_local.write(bytes_zip)
        _limpar_zips_locais_antigos()
        registrador.info(
            "[backup-db] cópia local salva: %s (%s bytes)",
            caminho,
            len(bytes_zip),
        )
        return caminho
    except OSError as erro_ao_gravar:
        registrador.error(
            "[backup-db] falha ao gravar ZIP local %s: %s",
            nome_arquivo,
            erro_ao_gravar,
        )
        return None


def _montar_bytes_do_zip(snapshot: dict[str, Any]) -> tuple[bytes, bytes]:
    """
    Serializa o snapshot em JSON e compacta em ZIP.

    Devolve (bytes_json, bytes_zip) para log de tamanho e upload.
    """
    # indent=None deixa o JSON bem menor que indent=2 (menos chance de
    # estourar o limite do Discord depois de compactar).
    conteudo_json = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    bytes_json = conteudo_json.encode("utf-8")
    buffer_zip = io.BytesIO()
    with zipfile.ZipFile(
        buffer_zip,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as arquivo_zip:
        arquivo_zip.writestr(NOME_JSON_DENTRO_DO_ZIP, bytes_json)
    return bytes_json, buffer_zip.getvalue()


async def exportar_banco_para_canal(
    guilda: discord.Guild,
    *,
    autor: str | None = None,
    forcar: bool = False,
) -> dict[str, Any]:
    """
    Gera snapshot, grava ZIP no disco e envia ao LOG_BACKUP.

    Regras anti-perda:
      1. Sempre grava cópia local em BACKUP_DIR/database/ antes do Discord.
      2. Envia o **arquivo primeiro**; só depois posta o card.
      3. Só considera sucesso se a mensagem do arquivo tiver anexo de verdade.
      4. Se o ZIP passar do limite seguro do Discord, não tenta upload mentiroso:
         mantém o arquivo local e avisa no retorno.

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
            hash_canal = _extrair_hash_da_mensagem(ultimo) or ""
            if hash_canal and (
                hash_canal == hash_local
                or hash_canal == hash_local[:16]
                or hash_local.startswith(hash_canal)
            ):
                return {
                    "enviado": False,
                    "motivo": "sem alteração (hash igual ao último no canal)",
                    "hash": hash_local,
                    "mensagem_id": ultimo.id,
                }

    quantidade_tabelas, quantidade_linhas = _contar_linhas(snapshot)
    carimbo = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    hash_curto = (hash_local or "semhash")[:16]
    nome_arquivo = f"db_backup_{hash_curto}_{carimbo}.zip"

    bytes_json, bytes_zip = _montar_bytes_do_zip(snapshot)
    tamanho_bytes = len(bytes_zip)
    tamanho_json = len(bytes_json)

    registrador.info(
        "[backup-db] snapshot pronto: %s tabelas, %s linhas, "
        "json=%s bytes, zip=%s bytes",
        quantidade_tabelas,
        quantidade_linhas,
        tamanho_json,
        tamanho_bytes,
    )

    # 1) Cópia local SEMPRE — independente do Discord.
    caminho_local = _gravar_zip_local(nome_arquivo, bytes_zip)

    if tamanho_bytes > LIMITE_ANEXO_DISCORD_BYTES:
        motivo = (
            f"ZIP com {tamanho_bytes} bytes passa do limite seguro do Discord "
            f"({LIMITE_ANEXO_DISCORD_BYTES}). Cópia local: {caminho_local or 'falhou'}."
        )
        registrador.error("[backup-db] %s", motivo)
        return {
            "enviado": False,
            "motivo": motivo,
            "hash": hash_local,
            "tabelas": quantidade_tabelas,
            "linhas": quantidade_linhas,
            "arquivo": nome_arquivo,
            "caminho_local": caminho_local,
            "tamanho_bytes": tamanho_bytes,
        }

    # 2) Arquivo PRIMEIRO — sem card órfão se o upload falhar.
    mensagem_arquivo = None
    try:
        arquivo = discord.File(
            fp=io.BytesIO(bytes_zip),
            filename=nome_arquivo,
        )
        mensagem_arquivo = await canal.send(file=arquivo)
    except discord.HTTPException as erro_http:
        motivo = (
            f"Discord recusou o anexo ({erro_http}). "
            f"Cópia local: {caminho_local or 'falhou'}."
        )
        registrador.error("[backup-db] %s", motivo)
        return {
            "enviado": False,
            "motivo": motivo,
            "hash": hash_local,
            "tabelas": quantidade_tabelas,
            "linhas": quantidade_linhas,
            "arquivo": nome_arquivo,
            "caminho_local": caminho_local,
            "tamanho_bytes": tamanho_bytes,
        }
    except Exception as erro_envio:
        motivo = (
            f"Falha ao enviar o ZIP ao canal: {erro_envio}. "
            f"Cópia local: {caminho_local or 'falhou'}."
        )
        registrador.exception("[backup-db] %s", motivo)
        return {
            "enviado": False,
            "motivo": motivo,
            "hash": hash_local,
            "tabelas": quantidade_tabelas,
            "linhas": quantidade_linhas,
            "arquivo": nome_arquivo,
            "caminho_local": caminho_local,
            "tamanho_bytes": tamanho_bytes,
        }

    # Confirma que a mensagem realmente tem o anexo (anti card sem arquivo).
    if not mensagem_arquivo.attachments:
        motivo = (
            "Mensagem do backup foi criada SEM anexo. "
            f"Cópia local: {caminho_local or 'falhou'}."
        )
        registrador.error("[backup-db] %s", motivo)
        try:
            await mensagem_arquivo.delete()
        except discord.HTTPException:
            pass
        return {
            "enviado": False,
            "motivo": motivo,
            "hash": hash_local,
            "tabelas": quantidade_tabelas,
            "linhas": quantidade_linhas,
            "arquivo": nome_arquivo,
            "caminho_local": caminho_local,
            "tamanho_bytes": tamanho_bytes,
        }

    # 3) Card só depois do arquivo confirmado.
    mensagem_card = None
    try:
        view_card = _montar_card_backup_db(
            guilda,
            snapshot,
            autor=autor,
            nome_arquivo=nome_arquivo,
            tamanho_bytes=tamanho_bytes,
        )
        mensagem_card = await canal.send(view=view_card)
    except Exception as erro_card:
        # Arquivo já está no canal — backup não se perdeu. Só o card falhou.
        registrador.warning(
            "[backup-db] arquivo ok, mas o card falhou: %s",
            erro_card,
        )

    return {
        "enviado": True,
        "motivo": "backup postado no LOG_BACKUP (arquivo + cópia local)",
        "hash": hash_local,
        "tabelas": quantidade_tabelas,
        "linhas": quantidade_linhas,
        "mensagem_id": mensagem_arquivo.id,
        "mensagem_card_id": (
            mensagem_card.id if mensagem_card is not None else None
        ),
        "arquivo": nome_arquivo,
        "canal_id": canal.id,
        "tamanho_bytes": tamanho_bytes,
        "caminho_local": caminho_local,
    }


async def obter_ultimo_backup_mensagem(
    canal: discord.TextChannel,
) -> discord.Message | None:
    """
    Última mensagem do canal que tem anexo .zip ou .json de backup de verdade.

    Card sem arquivo NÃO conta — era exatamente o bug que fazia parecer
    que havia backup quando o JSON não tinha sido enviado.
    """
    async for mensagem in canal.history(limit=80):
        anexo = _anexo_backup_da_mensagem(mensagem)
        if anexo is None:
            continue
        # Exige anexo real com tamanho > 0
        if (anexo.size or 0) <= 0:
            continue
        nome = (anexo.filename or "").lower()
        if nome.startswith("db_backup_"):
            return mensagem
        if nome.endswith(".zip") or nome.endswith(".json"):
            return mensagem
    return None


async def listar_backups_do_canal(
    guilda: discord.Guild,
    *,
    limite: int = 10,
) -> list[dict[str, Any]]:
    """
    Localiza anexos JSON recentes no canal configurado como cofre do banco.

    O limite controla quantas mensagens válidas entram na lista, que inclui nomes,
    hashes, links e metadados do anexo. Examina somente o histórico recente e
    retorna uma lista vazia se o canal não estiver configurado, evitando falhas na
    interface administrativa.
    """
    canal = _canal_log_backup(guilda)
    if canal is None:
        return []

    encontrados: list[dict[str, Any]] = []
    async for mensagem in canal.history(limit=80):
        anexo = _anexo_backup_da_mensagem(mensagem)
        if anexo is None:
            continue
        nome = (anexo.filename or "").lower()
        if not (
            nome.startswith("db_backup_")
            or MARCADOR_BACKUP_DB in (mensagem.content or "")
            or nome.endswith(".zip")
            or nome.endswith(".json")
        ):
            continue
        encontrados.append(
            {
                "mensagem_id": mensagem.id,
                "criado_em": mensagem.created_at.isoformat(),
                "hash": _extrair_hash_da_mensagem(mensagem),
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
    """
    Informa se o retrato atual do banco corresponde ao último cofre do Discord.

    Gera o hash local e o compara ao hash encontrado no anexo mais recente, sem
    gravar nem alterar registros. O dicionário retornado traz contagens, hashes e,
    quando houver, o link da mensagem, para que a interface explique divergências
    antes de sugerir exportação ou importação.
    """
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

    hash_canal = _extrair_hash_da_mensagem(ultimo)
    igual = bool(
        hash_canal
        and (
            hash_canal == hash_local
            or hash_canal == hash_local[:16]
            or hash_local.startswith(hash_canal)
        )
    )
    return {
        "ok": True,
        "igual": igual,
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
    """
    Painel ephemeral admin do cofre do banco (Components V2).

    Sem timeout: fica aberto até o admin descartar a mensagem ephemeral.
    Sem custom_id global: painel é só desta mensagem (evita clique em
    mensagem antiga após restart).
    """

    def __init__(self, bot: discord.Client, membro_id: int):
        # timeout=None → painel admin não “morre” em 5 minutos
        super().__init__(timeout=None)
        self.bot = bot
        self.membro_id = membro_id

        linha_principal = discord.ui.ActionRow()
        botao_exportar = discord.ui.Button(
            label="Exportar (se mudou)",
            style=discord.ButtonStyle.success,
            emoji="📤",
        )
        botao_exportar.callback = self._ao_exportar
        botao_forcar = discord.ui.Button(
            label="Exportar forçado",
            style=discord.ButtonStyle.success,
            emoji="📦",
        )
        botao_forcar.callback = self._ao_exportar_forcado
        linha_principal.add_item(botao_exportar)
        linha_principal.add_item(botao_forcar)

        linha_secundaria = discord.ui.ActionRow()
        botao_verificar = discord.ui.Button(
            label="Verificar",
            style=discord.ButtonStyle.primary,
            emoji="🔍",
        )
        botao_verificar.callback = self._ao_verificar
        botao_listar = discord.ui.Button(
            label="Listar",
            style=discord.ButtonStyle.secondary,
            emoji="📋",
        )
        botao_listar.callback = self._ao_listar
        linha_secundaria.add_item(botao_verificar)
        linha_secundaria.add_item(botao_listar)

        linha_importar = discord.ui.ActionRow()
        botao_importar = discord.ui.Button(
            label="Importar ZIP (próxima msg)",
            style=discord.ButtonStyle.danger,
            emoji="📥",
        )
        botao_importar.callback = self._ao_importar
        linha_importar.add_item(botao_importar)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# 🗄️ Painel — Backup do banco\n"
                    "Só **você** vê esta mensagem (ephemeral).\n"
                    "Cofre = canal **LOG_BACKUP** + cópia local em disco.\n"
                    "Agenda automática (Brasília): **00:00**, **11:00**, **17:00**.\n"
                    "Formato: **`.zip`** (JSON compactado). Import é **só aditivo**."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(
                    "## Ações\n"
                    "• **Exportar (se mudou)** — só posta se o hash mudou.\n"
                    "• **Exportar forçado** — posta mesmo com hash igual.\n"
                    "• **Verificar** — banco local × último ZIP do canal.\n"
                    "• **Listar** — últimos arquivos com link de download.\n"
                    "• **Importar** — envie o `.zip`/`.json` em até 90s.\n"
                    "• Atalho com anexo: `/backup banco-importar`"
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha_principal,
                linha_secundaria,
                linha_importar,
                accent_color=discord.Color.dark_teal(),
            )
        )

    def _autor_ok(self, interacao: discord.Interaction) -> bool:
        return interacao.user.id == self.membro_id

    async def _exportar_interno(
        self,
        interacao: discord.Interaction,
        *,
        forcar: bool,
    ) -> None:
        """Exporta o snapshot; usado pelos dois botões de exportar."""
        if not self._autor_ok(interacao):
            await responder_erro(
                interacao,
                titulo="Não é o seu painel",
                linhas=["Só quem abriu o painel pode usar os botões."],
            )
            return
        # Ephemeral + thinking: a geração do snapshot passa dos 3s fácil.
        await interacao.response.defer(thinking=True, ephemeral=True)
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
                forcar=forcar,
            )
            if resultado.get("enviado"):
                caminho_local = resultado.get("caminho_local") or "—"
                await responder_sucesso(
                    interacao,
                    titulo="Backup enviado ao canal",
                    linhas=[
                        f"Arquivo: `{resultado.get('arquivo')}`",
                        f"Hash: `{str(resultado.get('hash') or '')[:16]}…`",
                        f"Tabelas: **{resultado.get('tabelas')}** · "
                        f"Linhas: **{resultado.get('linhas')}**",
                        f"Canal: <#{resultado.get('canal_id')}>",
                        f"Cópia local: `{caminho_local}`",
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

    async def _ao_exportar(self, interacao: discord.Interaction):
        await self._exportar_interno(interacao, forcar=False)

    async def _ao_exportar_forcado(self, interacao: discord.Interaction):
        await self._exportar_interno(interacao, forcar=True)

    async def _ao_verificar(self, interacao: discord.Interaction):
        if not self._autor_ok(interacao):
            await responder_erro(
                interacao,
                titulo="Não é o seu painel",
                linhas=["Só quem abriu o painel pode usar os botões."],
            )
            return
        await interacao.response.defer(thinking=True, ephemeral=True)
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
                        f"Hash canal: "
                        f"`{str(resultado.get('hash_canal') or 'nenhum')[:20]}"
                        f"…`",
                        f"Tabelas: **{resultado.get('tabelas')}** · "
                        f"Linhas: **{resultado.get('linhas')}**",
                        "Use **Exportar** para atualizar o canal, ou **Importar** se "
                        "o canal estiver mais completo.",
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
        await interacao.response.defer(thinking=True, ephemeral=True)
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
                        f"`{MARCADOR_BACKUP_DB}` e anexo `.zip`.",
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
            titulo="Envie o arquivo de backup",
            linhas=[
                "Nas **próximas 90 segundos**, mande neste canal (ou em DM comigo) "
                "uma mensagem **só com o anexo** `.zip` (ou `.json` legado).",
                "O bot lê o arquivo e **só adiciona** linhas que faltam no banco "
                "(nunca apaga o que já existe).",
                "Atalho: `/backup banco-importar` + anexo.",
            ],
            delay=90,
        )

        def _filtro(mensagem: discord.Message) -> bool:
            if mensagem.author.id != interacao.user.id:
                return False
            return _anexo_backup_da_mensagem(mensagem) is not None

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
                    "Nenhum `.zip`/`.json` recebido em 90s.",
                    "Tente de novo ou use `/backup banco-importar`.",
                ],
                delay=15,
            )
            return

        anexo = _anexo_backup_da_mensagem(mensagem_arquivo)
        if anexo is None:
            await responder_erro(
                interacao,
                titulo="Anexo inválido",
                linhas=["Não achei um arquivo `.zip` ou `.json` na mensagem."],
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
