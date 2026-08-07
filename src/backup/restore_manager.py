"""Restauração cautelosa a partir de um backup.

Princípios:
  1. Só CRIA ou ATUALIZA — nunca apaga cargos/canais.
  2. dry_run=True lista o que faria, sem tocar no Discord.
  3. Cargos managed (integração, booster) são ignorados.
  4. Pausa entre escritas para respeitar rate limit.
  5. Se o cargo sumiu (id novo), tenta achar pelo NOME.
"""

from __future__ import annotations

import asyncio

import discord

ATRASO_RATE_LIMIT = 1.0


class RestoreManager:
    """Aplica (ou simula) a restauração de partes de um backup."""

    def __init__(self) -> None:
        self.relatorio: list[str] = []

    def _anotar(self, mensagem: str) -> None:
        self.relatorio.append(mensagem)

    async def restaurar_cargos(
        self,
        guild: discord.Guild,
        backup: dict,
        dry_run: bool = False,
    ) -> list[str]:
        self.relatorio = []
        cargos_por_id = {cargo.id: cargo for cargo in guild.roles}
        cargos_por_nome = {
            cargo.name: cargo for cargo in guild.roles if not cargo.is_default()
        }
        cargos_do_backup = sorted(
            backup.get("roles", []), key=lambda item: item.get("position", 0)
        )

        for dados_cargo in cargos_do_backup:
            if dados_cargo.get("managed"):
                continue

            cargo_existente = cargos_por_id.get(dados_cargo["id"])
            if cargo_existente is None:
                cargo_existente = cargos_por_nome.get(dados_cargo["name"])

            permissoes = discord.Permissions(dados_cargo["permissions"])
            cor = discord.Color(dados_cargo["color"])

            if cargo_existente is None:
                self._anotar(f"➕ Criar cargo `{dados_cargo['name']}`")
                if not dry_run:
                    await guild.create_role(
                        name=dados_cargo["name"],
                        permissions=permissoes,
                        colour=cor,
                        hoist=dados_cargo.get("hoist", False),
                        mentionable=dados_cargo.get("mentionable", False),
                        reason="Restauração de backup",
                    )
                    await asyncio.sleep(ATRASO_RATE_LIMIT)
                continue

            mudou = (
                cargo_existente.name != dados_cargo["name"]
                or cargo_existente.permissions.value != dados_cargo["permissions"]
                or cargo_existente.color.value != dados_cargo["color"]
                or cargo_existente.hoist != dados_cargo.get("hoist", False)
                or cargo_existente.mentionable != dados_cargo.get("mentionable", False)
            )
            if mudou:
                self._anotar(f"✏️ Atualizar cargo `{cargo_existente.name}`")
                if not dry_run:
                    try:
                        await cargo_existente.edit(
                            name=dados_cargo["name"],
                            permissions=permissoes,
                            colour=cor,
                            hoist=dados_cargo.get("hoist", False),
                            mentionable=dados_cargo.get("mentionable", False),
                            reason="Restauração de backup",
                        )
                    except discord.Forbidden:
                        self._anotar(
                            f"⚠️ Sem permissão para editar `{cargo_existente.name}` "
                            "(cargo do bot precisa estar acima na hierarquia)."
                        )
                    await asyncio.sleep(ATRASO_RATE_LIMIT)

        if not self.relatorio:
            self._anotar("Nenhuma alteração necessária nos cargos.")
        return self.relatorio

    async def restaurar_categorias(
        self,
        guild: discord.Guild,
        backup: dict,
        dry_run: bool = False,
    ) -> list[str]:
        self.relatorio = []
        categorias_por_id = {cat.id: cat for cat in guild.categories}
        categorias_por_nome = {cat.name: cat for cat in guild.categories}

        for dados in sorted(
            backup.get("categories", []), key=lambda item: item.get("position", 0)
        ):
            categoria = categorias_por_id.get(dados["id"]) or categorias_por_nome.get(
                dados["name"]
            )
            if categoria is None:
                self._anotar(f"➕ Criar categoria `{dados['name']}`")
                if not dry_run:
                    await guild.create_category(
                        name=dados["name"], reason="Restauração de backup"
                    )
                    await asyncio.sleep(ATRASO_RATE_LIMIT)
            elif categoria.name != dados["name"]:
                self._anotar(
                    f"✏️ Renomear categoria `{categoria.name}` → `{dados['name']}`"
                )
                if not dry_run:
                    await categoria.edit(
                        name=dados["name"], reason="Restauração de backup"
                    )
                    await asyncio.sleep(ATRASO_RATE_LIMIT)

        if not self.relatorio:
            self._anotar("Nenhuma alteração necessária nas categorias.")
        return self.relatorio

    async def restaurar_canais(
        self,
        guild: discord.Guild,
        backup: dict,
        dry_run: bool = False,
    ) -> list[str]:
        self.relatorio = []
        canais_por_id = {
            canal.id: canal
            for canal in guild.channels
            if not isinstance(canal, discord.CategoryChannel)
        }
        canais_por_nome = {
            canal.name: canal
            for canal in guild.channels
            if not isinstance(canal, discord.CategoryChannel)
        }
        categorias_por_id = {cat.id: cat for cat in guild.categories}

        for dados in sorted(
            backup.get("channels", []), key=lambda item: item.get("position", 0)
        ):
            canal = canais_por_id.get(dados["id"]) or canais_por_nome.get(dados["name"])
            categoria = categorias_por_id.get(dados.get("category_id"))
            tipo = dados.get("type", "text")

            if canal is None:
                self._anotar(f"➕ Criar canal `{dados['name']}` ({tipo})")
                if not dry_run:
                    if "voice" in tipo:
                        await guild.create_voice_channel(
                            name=dados["name"],
                            category=categoria,
                            reason="Restauração de backup",
                        )
                    else:
                        await guild.create_text_channel(
                            name=dados["name"],
                            category=categoria,
                            topic=dados.get("topic"),
                            nsfw=dados.get("nsfw", False),
                            reason="Restauração de backup",
                        )
                    await asyncio.sleep(ATRASO_RATE_LIMIT)
                continue

            mudou_nome = canal.name != dados["name"]
            mudou_extra = False
            if isinstance(canal, discord.TextChannel):
                mudou_extra = (
                    canal.topic != dados.get("topic")
                    or canal.nsfw != dados.get("nsfw", False)
                )
            if mudou_nome or mudou_extra:
                self._anotar(f"✏️ Atualizar canal `{canal.name}`")
                if not dry_run:
                    argumentos: dict = {
                        "name": dados["name"],
                        "reason": "Restauração de backup",
                    }
                    if isinstance(canal, discord.TextChannel):
                        argumentos["topic"] = dados.get("topic")
                        argumentos["nsfw"] = dados.get("nsfw", False)
                    await canal.edit(**argumentos)
                    await asyncio.sleep(ATRASO_RATE_LIMIT)

        if not self.relatorio:
            self._anotar("Nenhuma alteração necessária nos canais.")
        return self.relatorio

    async def restaurar_membros(
        self,
        guild: discord.Guild,
        backup: dict,
        dry_run: bool = False,
    ) -> list[str]:
        """Reaplica cargos/apelidos para quem ainda está no servidor."""
        self.relatorio = []
        cargos_por_id = {cargo.id: cargo for cargo in guild.roles}
        cargos_por_nome = {
            cargo.name: cargo for cargo in guild.roles if not cargo.is_default()
        }

        for dados_membro in backup.get("members", []):
            membro = guild.get_member(dados_membro["id"])
            if membro is None:
                continue

            ids_desejados = set(dados_membro.get("role_ids") or [])
            nomes_desejados = dados_membro.get("role_names") or []

            cargos_alvo: list[discord.Role] = []
            for role_id in ids_desejados:
                cargo = cargos_por_id.get(role_id)
                if cargo and not cargo.is_default() and not cargo.managed:
                    cargos_alvo.append(cargo)
            for nome in nomes_desejados:
                cargo = cargos_por_nome.get(nome)
                if (
                    cargo
                    and cargo not in cargos_alvo
                    and not cargo.is_default()
                    and not cargo.managed
                ):
                    cargos_alvo.append(cargo)

            ids_alvo = {cargo.id for cargo in cargos_alvo}
            ids_atuais = {
                cargo.id
                for cargo in membro.roles
                if not cargo.is_default() and not cargo.managed
            }

            para_adicionar = [c for c in cargos_alvo if c.id not in ids_atuais]
            para_remover = [
                cargos_por_id[role_id]
                for role_id in ids_atuais - ids_alvo
                if role_id in cargos_por_id and not cargos_por_id[role_id].managed
            ]

            if para_adicionar or para_remover:
                self._anotar(
                    f"👤 {dados_membro.get('name', membro.id)}: "
                    f"+{len(para_adicionar)} cargo(s), -{len(para_remover)} cargo(s)"
                )
                if not dry_run:
                    try:
                        if para_adicionar:
                            await membro.add_roles(
                                *para_adicionar, reason="Restauração de backup"
                            )
                        if para_remover:
                            await membro.remove_roles(
                                *para_remover, reason="Restauração de backup"
                            )
                    except discord.Forbidden:
                        self._anotar(f"⚠️ Sem permissão para alterar cargos de {membro}.")
                    await asyncio.sleep(ATRASO_RATE_LIMIT)

            apelido_backup = dados_membro.get("nickname")
            if apelido_backup != membro.nick:
                self._anotar(
                    f"👤 {dados_membro.get('name', membro.id)}: "
                    f"apelido `{membro.nick}` → `{apelido_backup}`"
                )
                if not dry_run:
                    try:
                        await membro.edit(
                            nick=apelido_backup, reason="Restauração de backup"
                        )
                    except discord.Forbidden:
                        self._anotar(f"⚠️ Sem permissão para renomear {membro}.")
                    await asyncio.sleep(ATRASO_RATE_LIMIT)

        if not self.relatorio:
            self._anotar("Nenhuma alteração necessária nos membros.")
        return self.relatorio

    async def restaurar_tudo(
        self,
        guild: discord.Guild,
        backup: dict,
        dry_run: bool = False,
    ) -> dict[str, list[str]]:
        return {
            "roles": await self.restaurar_cargos(guild, backup, dry_run),
            "categories": await self.restaurar_categorias(guild, backup, dry_run),
            "channels": await self.restaurar_canais(guild, backup, dry_run),
        }
