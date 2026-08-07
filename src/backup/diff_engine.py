# src/backup/diff_engine.py
"""
Motor de comparação entre um backup salvo e o estado atual do servidor.

Não muda nada no Discord — só responde o que falta, o que é novo e o que mudou.
"""

from __future__ import annotations

import discord

from src.backup.backup_manager import BackupManager


class DiffEngine:
    """Compara backup × servidor atual, campo a campo."""

    def __init__(self) -> None:
        self.gerenciador = BackupManager()

    def comparar(self, guilda: discord.Guild, backup: dict) -> dict:
        """
        Gera um dicionário de diferenças por categoria
        (cargos, categorias, canais, emojis).
        """
        estado_atual = self.gerenciador.criar_backup(
            guilda, criado_por="diff-temporario"
        )

        return {
            "cargos": self._diff_lista(
                lista_backup=backup.get("roles", []),
                lista_atual=estado_atual["roles"],
                chave="id",
                campos=[
                    "name",
                    "color",
                    "permissions",
                    "hoist",
                    "mentionable",
                    "position",
                ],
            ),
            "categorias": self._diff_lista(
                lista_backup=backup.get("categories", []),
                lista_atual=estado_atual["categories"],
                chave="id",
                campos=["name", "position"],
            ),
            "canais": self._diff_lista(
                lista_backup=backup.get("channels", []),
                lista_atual=estado_atual["channels"],
                chave="id",
                campos=[
                    "name",
                    "type",
                    "category_id",
                    "position",
                    "topic",
                    "nsfw",
                    "slowmode_delay",
                ],
            ),
            "emojis": self._diff_lista(
                lista_backup=backup.get("emojis", []),
                lista_atual=estado_atual["emojis"],
                chave="id",
                campos=["name"],
            ),
        }

    @staticmethod
    def _diff_lista(
        lista_backup: list[dict],
        lista_atual: list[dict],
        chave: str,
        campos: list[str],
    ) -> dict:
        mapa_backup = {item[chave]: item for item in lista_backup}
        mapa_atual = {item[chave]: item for item in lista_atual}

        faltando_no_atual = [
            mapa_backup[identificador]
            for identificador in mapa_backup
            if identificador not in mapa_atual
        ]
        novo_no_atual = [
            mapa_atual[identificador]
            for identificador in mapa_atual
            if identificador not in mapa_backup
        ]

        modificados: list[dict] = []
        for identificador in mapa_backup:
            if identificador not in mapa_atual:
                continue

            item_backup = mapa_backup[identificador]
            item_atual = mapa_atual[identificador]
            diferencas: dict = {}

            for campo in campos:
                valor_no_backup = item_backup.get(campo)
                valor_atual = item_atual.get(campo)
                if valor_no_backup != valor_atual:
                    diferencas[campo] = {
                        "backup": valor_no_backup,
                        "atual": valor_atual,
                    }

            if diferencas:
                modificados.append(
                    {
                        "id": identificador,
                        "name": item_backup.get("name", "?"),
                        "diffs": diferencas,
                    }
                )

        return {
            "faltando_no_atual": faltando_no_atual,
            "novo_no_atual": novo_no_atual,
            "modificado": modificados,
        }

    @staticmethod
    def resumir(diff: dict) -> str:
        """Transforma o dicionário de diff em texto legível para o card."""
        linhas: list[str] = []

        for categoria, resultado in diff.items():
            quantidade_faltando = len(resultado["faltando_no_atual"])
            quantidade_novo = len(resultado["novo_no_atual"])
            quantidade_modificado = len(resultado["modificado"])

            tem_diferenca = (
                quantidade_faltando or quantidade_novo or quantidade_modificado
            )
            if tem_diferenca:
                linhas.append(
                    f"**{categoria.capitalize()}**: "
                    f"{quantidade_faltando} ausentes, "
                    f"{quantidade_novo} novos, "
                    f"{quantidade_modificado} modificados"
                )

        if not linhas:
            return "✅ Nenhuma diferença encontrada. O servidor está igual ao backup."

        return "\n".join(linhas)
