"""Gerenciador de backup estrutural do servidor Discord.

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

from src.config import BACKUP_DIR, MAX_BACKUPS_PER_GUILD


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
            tipo_alvo = "role" if isinstance(alvo, discord.Role) else "member"
            permitir, negar = permissao.pair()
            resultado.append(
                {
                    "target_type": tipo_alvo,
                    "target_id": alvo.id,
                    "target_name": getattr(alvo, "name", str(alvo.id)),
                    "allow": permitir.value,
                    "deny": negar.value,
                }
            )
        return resultado

    def _serializar_cargos(self, guild: discord.Guild) -> list[dict]:
        lista: list[dict] = []
        for cargo in guild.roles:
            if cargo.is_default():
                continue
            lista.append(
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
        return lista

    def _serializar_categorias(self, guild: discord.Guild) -> list[dict]:
        lista: list[dict] = []
        for categoria in guild.categories:
            lista.append(
                {
                    "id": categoria.id,
                    "name": categoria.name,
                    "position": categoria.position,
                    "overwrites": self._serializar_overwrites(categoria.overwrites),
                }
            )
        return lista

    def _serializar_canais(self, guild: discord.Guild) -> list[dict]:
        lista: list[dict] = []
        for canal in guild.channels:
            if isinstance(canal, discord.CategoryChannel):
                continue
            dados: dict[str, Any] = {
                "id": canal.id,
                "name": canal.name,
                "type": str(canal.type),
                "category_id": canal.category_id,
                "position": canal.position,
                "overwrites": self._serializar_overwrites(canal.overwrites),
            }
            if isinstance(canal, discord.TextChannel):
                dados["topic"] = canal.topic
                dados["nsfw"] = canal.nsfw
                dados["slowmode_delay"] = canal.slowmode_delay
            elif isinstance(canal, discord.VoiceChannel):
                dados["bitrate"] = canal.bitrate
                dados["user_limit"] = canal.user_limit
            elif isinstance(canal, discord.ForumChannel):
                dados["topic"] = canal.topic
                dados["nsfw"] = canal.nsfw
                dados["slowmode_delay"] = canal.slowmode_delay
            lista.append(dados)
        return lista

    def _serializar_emojis(self, guild: discord.Guild) -> list[dict]:
        return [
            {
                "id": emoji.id,
                "name": emoji.name,
                "url": str(emoji.url),
                "animated": emoji.animated,
            }
            for emoji in guild.emojis
        ]

    def _serializar_configuracoes(self, guild: discord.Guild) -> dict:
        return {
            "name": guild.name,
            "icon_url": str(guild.icon.url) if guild.icon else None,
            "banner_url": str(guild.banner.url) if guild.banner else None,
            "afk_timeout": guild.afk_timeout,
            "afk_channel_id": guild.afk_channel.id if guild.afk_channel else None,
            "verification_level": str(guild.verification_level),
            "explicit_content_filter": str(guild.explicit_content_filter),
        }

    def _serializar_membros(self, guild: discord.Guild) -> list[dict]:
        lista: list[dict] = []
        for membro in guild.members:
            if membro.bot:
                continue
            cargos_uteis = [cargo for cargo in membro.roles if not cargo.is_default()]
            lista.append(
                {
                    "id": membro.id,
                    "name": str(membro),
                    "nickname": membro.nick,
                    "role_ids": [cargo.id for cargo in cargos_uteis],
                    "role_names": [cargo.name for cargo in cargos_uteis],
                }
            )
        return lista

    def criar_backup(self, guild: discord.Guild, criado_por: str) -> dict:
        """Monta o snapshot completo do servidor (ainda não grava em disco)."""
        return {
            "guild_id": guild.id,
            "guild_name": guild.name,
            "created_at": datetime.datetime.utcnow().isoformat(),
            "created_by": criado_por,
            "roles": self._serializar_cargos(guild),
            "categories": self._serializar_categorias(guild),
            "channels": self._serializar_canais(guild),
            "emojis": self._serializar_emojis(guild),
            "server_settings": self._serializar_configuracoes(guild),
            "members": self._serializar_membros(guild),
        }

    def salvar_backup(self, backup: dict) -> str:
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
