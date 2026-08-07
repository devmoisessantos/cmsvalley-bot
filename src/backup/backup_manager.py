# src/backup/backup_manager.py
"""
Gerenciador de backup estrutural do servidor Discord.

Pense neste arquivo como o "fotógrafo" do servidor:
ele olha cargos, canais, categorias, emojis, configurações e membros,
e grava tudo num arquivo JSON que depois pode ser comparado ou restaurado.

Não altera nada no Discord — só lê e salva em disco.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any

import discord

from src.config import (
    BACKUP_DIR,
    MAX_BACKUPS_PER_GUILD,
)


class BackupManager:
    """Serializa o estado do servidor e grava/lê backups em JSON."""

    def __init__(self) -> None:
        os.makedirs(BACKUP_DIR, exist_ok=True)

    def _pasta_do_servidor(self, guild_id: int) -> str:
        """Cada servidor tem a sua pasta: data/backups/<guild_id>/."""
        caminho = os.path.join(BACKUP_DIR, str(guild_id))
        os.makedirs(caminho, exist_ok=True)
        return caminho

    @staticmethod
    def _serializar_overwrites(
        overwrites: dict[Any, discord.PermissionOverwrite],
    ) -> list[dict]:
        """Converte permissões de canal/categoria para lista gravável em JSON."""
        resultado: list[dict] = []

        for alvo, permissao in overwrites.items():
            tipo_do_alvo = "role" if isinstance(alvo, discord.Role) else "member"
            permitir, negar = permissao.pair()
            resultado.append(
                {
                    "target_type": tipo_do_alvo,
                    "target_id": alvo.id,
                    "target_name": getattr(alvo, "name", str(alvo.id)),
                    "allow": permitir.value,
                    "deny": negar.value,
                }
            )

        return resultado

    def _serializar_cargos(self, guilda: discord.Guild) -> list[dict]:
        lista_de_cargos: list[dict] = []

        for cargo in guilda.roles:
            if cargo.is_default():
                continue
            lista_de_cargos.append(
                {
                    "id": cargo.id,
                    "name": cargo.name,
                    "color": cargo.color.value,
                    "hoist": cargo.hoist,
                    "mentionable": cargo.mentionable,
                    "permissions": cargo.permissions.value,
                    "position": cargo.position,
                    "managed": cargo.managed,
                }
            )

        return lista_de_cargos

    def _serializar_categorias(self, guilda: discord.Guild) -> list[dict]:
        lista_de_categorias: list[dict] = []

        for categoria in guilda.categories:
            lista_de_categorias.append(
                {
                    "id": categoria.id,
                    "name": categoria.name,
                    "position": categoria.position,
                    "overwrites": self._serializar_overwrites(categoria.overwrites),
                }
            )

        return lista_de_categorias

    def _serializar_canais(self, guilda: discord.Guild) -> list[dict]:
        lista_de_canais: list[dict] = []

        for canal in guilda.channels:
            if isinstance(canal, discord.CategoryChannel):
                continue

            dados_do_canal: dict[str, Any] = {
                "id": canal.id,
                "name": canal.name,
                "type": str(canal.type),
                "category_id": canal.category_id,
                "position": canal.position,
                "overwrites": self._serializar_overwrites(canal.overwrites),
            }

            if isinstance(canal, discord.TextChannel):
                dados_do_canal["topic"] = canal.topic
                dados_do_canal["nsfw"] = canal.nsfw
                dados_do_canal["slowmode_delay"] = canal.slowmode_delay
            elif isinstance(canal, discord.VoiceChannel):
                dados_do_canal["bitrate"] = canal.bitrate
                dados_do_canal["user_limit"] = canal.user_limit
            elif isinstance(canal, discord.ForumChannel):
                dados_do_canal["topic"] = canal.topic
                dados_do_canal["nsfw"] = canal.nsfw
                dados_do_canal["slowmode_delay"] = canal.slowmode_delay

            lista_de_canais.append(dados_do_canal)

        return lista_de_canais

    def _serializar_emojis(self, guilda: discord.Guild) -> list[dict]:
        return [
            {
                "id": emoji.id,
                "name": emoji.name,
                "url": str(emoji.url),
                "animated": emoji.animated,
            }
            for emoji in guilda.emojis
        ]

    def _serializar_configuracoes(self, guilda: discord.Guild) -> dict:
        return {
            "name": guilda.name,
            "icon_url": str(guilda.icon.url) if guilda.icon else None,
            "banner_url": str(guilda.banner.url) if guilda.banner else None,
            "afk_timeout": guilda.afk_timeout,
            "afk_channel_id": (guilda.afk_channel.id if guilda.afk_channel else None),
            "verification_level": str(guilda.verification_level),
            "explicit_content_filter": str(guilda.explicit_content_filter),
        }

    def _serializar_membros(self, guilda: discord.Guild) -> list[dict]:
        lista_de_membros: list[dict] = []

        for membro in guilda.members:
            if membro.bot:
                continue

            cargos_uteis = [cargo for cargo in membro.roles if not cargo.is_default()]
            lista_de_membros.append(
                {
                    "id": membro.id,
                    "name": str(membro),
                    "nickname": membro.nick,
                    "role_ids": [cargo.id for cargo in cargos_uteis],
                    "role_names": [cargo.name for cargo in cargos_uteis],
                }
            )

        return lista_de_membros

    def criar_backup(self, guild: discord.Guild, criado_por: str) -> dict:
        """Monta o snapshot completo do servidor (ainda não grava em disco)."""
        momento_atual = datetime.datetime.now(datetime.timezone.utc).isoformat()

        return {
            "guild_id": guild.id,
            "guild_name": guild.name,
            "created_at": momento_atual,
            "created_by": criado_por,
            "roles": self._serializar_cargos(guild),
            "categories": self._serializar_categorias(guild),
            "channels": self._serializar_canais(guild),
            "emojis": self._serializar_emojis(guild),
            "server_settings": self._serializar_configuracoes(guild),
            "members": self._serializar_membros(guild),
        }

    def salvar_backup(self, backup: dict) -> str:
        """Grava o snapshot em JSON e limpa backups antigos se passar do limite."""
        pasta = self._pasta_do_servidor(backup["guild_id"])
        carimbo = backup["created_at"].replace(":", "-")
        nome_arquivo = f"backup_{carimbo}.json"
        caminho = os.path.join(pasta, nome_arquivo)

        with open(caminho, "w", encoding="utf-8") as arquivo:
            json.dump(backup, arquivo, indent=2, ensure_ascii=False)

        self._limpar_backups_antigos(backup["guild_id"])
        return caminho

    def _limpar_backups_antigos(self, guild_id: int) -> None:
        pasta = self._pasta_do_servidor(guild_id)
        arquivos = sorted(
            [nome for nome in os.listdir(pasta) if nome.endswith(".json")],
            reverse=True,
        )
        for nome_antigo in arquivos[MAX_BACKUPS_PER_GUILD:]:
            os.remove(os.path.join(pasta, nome_antigo))

    def listar_backups(self, guild_id: int) -> list[str]:
        pasta = self._pasta_do_servidor(guild_id)
        return sorted(
            [nome for nome in os.listdir(pasta) if nome.endswith(".json")],
            reverse=True,
        )

    def carregar_backup(self, guild_id: int, nome_arquivo: str) -> dict | None:
        caminho = os.path.join(self._pasta_do_servidor(guild_id), nome_arquivo)
        if not os.path.exists(caminho):
            return None
        with open(caminho, "r", encoding="utf-8") as arquivo:
            return json.load(arquivo)

    def deletar_backup(self, guild_id: int, nome_arquivo: str) -> bool:
        caminho = os.path.join(self._pasta_do_servidor(guild_id), nome_arquivo)
        if os.path.exists(caminho):
            os.remove(caminho)
            return True
        return False

    def nome_backup_mais_recente(self, guild_id: int) -> str | None:
        arquivos = self.listar_backups(guild_id)
        return arquivos[0] if arquivos else None

    def caminho_completo(self, guild_id: int, nome_arquivo: str) -> str:
        return os.path.join(self._pasta_do_servidor(guild_id), nome_arquivo)
