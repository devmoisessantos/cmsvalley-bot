# src/backup/restore_manager.py
"""
Restauração cautelosa a partir de um backup.

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
        guilda: discord.Guild,
        backup: dict,
        dry_run: bool = False,
    ) -> list[str]:
        """
        Recria ou atualiza cargos do retrato sem apagar os existentes.

        Procura cada cargo por identificador e, caso ele tenha mudado após recriação,
        também pelo nome. Com `dry_run` ativo, retorna apenas o relatório da prévia;
        caso contrário, cria ou edita cargos no Discord, ignora cargos gerenciados e
        pausa entre escritas para respeitar o limite da API.
        """
        self.relatorio = []
        cargos_por_id = {cargo.id: cargo for cargo in guilda.roles}
        cargos_por_nome = {
            cargo.name: cargo for cargo in guilda.roles if not cargo.is_default()
        }
        cargos_do_backup = sorted(
            backup.get("roles", []),
            key=lambda item: item.get("position", 0),
        )

        for dados_cargo in cargos_do_backup:
            if dados_cargo.get("managed"):
                continue

            cargo_existente = cargos_por_id.get(dados_cargo["id"])
            if cargo_existente is None:
                cargo_existente = cargos_por_nome.get(dados_cargo["name"])

            permissoes = discord.Permissions(dados_cargo["permissions"])
            cor_do_cargo = discord.Color(dados_cargo["color"])

            if cargo_existente is None:
                self._anotar(f"➕ Criar cargo `{dados_cargo['name']}`")
                if not dry_run:
                    await guilda.create_role(
                        name=dados_cargo["name"],
                        permissions=permissoes,
                        colour=cor_do_cargo,
                        hoist=dados_cargo.get("hoist", False),
                        mentionable=dados_cargo.get("mentionable", False),
                        reason="Restauração de backup",
                    )
                    await asyncio.sleep(ATRASO_RATE_LIMIT)
                continue

            cargo_mudou = (
                cargo_existente.name != dados_cargo["name"]
                or cargo_existente.permissions.value != dados_cargo["permissions"]
                or cargo_existente.color.value != dados_cargo["color"]
                or cargo_existente.hoist != dados_cargo.get("hoist", False)
                or cargo_existente.mentionable != dados_cargo.get("mentionable", False)
            )
            if cargo_mudou:
                self._anotar(f"✏️ Atualizar cargo `{cargo_existente.name}`")
                if not dry_run:
                    try:
                        await cargo_existente.edit(
                            name=dados_cargo["name"],
                            permissions=permissoes,
                            colour=cor_do_cargo,
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
        guilda: discord.Guild,
        backup: dict,
        dry_run: bool = False,
    ) -> list[str]:
        """
        Recupera categorias ausentes ou renomeadas sem remover as atuais.

        Compara identificadores e nomes para suportar uma categoria recriada pelo
        Discord. O modo `dry_run` produz somente a lista de mudanças; fora dele,
        cria ou edita categorias e aguarda entre as operações para evitar limites
        da API, devolvendo um relatório inclusive quando nada precisa mudar.
        """
        self.relatorio = []
        categorias_por_id = {categoria.id: categoria for categoria in guilda.categories}
        categorias_por_nome = {
            categoria.name: categoria for categoria in guilda.categories
        }

        for dados_categoria in sorted(
            backup.get("categories", []),
            key=lambda item: item.get("position", 0),
        ):
            categoria = categorias_por_id.get(
                dados_categoria["id"]
            ) or categorias_por_nome.get(dados_categoria["name"])

            if categoria is None:
                self._anotar(f"➕ Criar categoria `{dados_categoria['name']}`")
                if not dry_run:
                    await guilda.create_category(
                        name=dados_categoria["name"],
                        reason="Restauração de backup",
                    )
                    await asyncio.sleep(ATRASO_RATE_LIMIT)
            elif categoria.name != dados_categoria["name"]:
                self._anotar(
                    f"✏️ Renomear categoria `{categoria.name}` "
                    f"→ `{dados_categoria['name']}`"
                )
                if not dry_run:
                    await categoria.edit(
                        name=dados_categoria["name"],
                        reason="Restauração de backup",
                    )
                    await asyncio.sleep(ATRASO_RATE_LIMIT)

        if not self.relatorio:
            self._anotar("Nenhuma alteração necessária nas categorias.")
        return self.relatorio

    async def restaurar_canais(
        self,
        guilda: discord.Guild,
        backup: dict,
        dry_run: bool = False,
    ) -> list[str]:
        """
        Recompõe canais ausentes e atributos básicos dos que ainda existem.

        Localiza canais por identificador ou nome para tolerar recriações, associa
        categorias quando ainda podem ser encontradas e trata texto e voz de forma
        apropriada. Em prévia não toca no Discord; na execução cria ou edita canais
        sem excluí-los e espaça as escritas para não exceder a API.
        """
        self.relatorio = []
        canais_por_id = {
            canal.id: canal
            for canal in guilda.channels
            if not isinstance(canal, discord.CategoryChannel)
        }
        canais_por_nome = {
            canal.name: canal
            for canal in guilda.channels
            if not isinstance(canal, discord.CategoryChannel)
        }
        categorias_por_id = {categoria.id: categoria for categoria in guilda.categories}

        for dados_canal in sorted(
            backup.get("channels", []),
            key=lambda item: item.get("position", 0),
        ):
            canal = canais_por_id.get(dados_canal["id"]) or canais_por_nome.get(
                dados_canal["name"]
            )
            categoria = categorias_por_id.get(dados_canal.get("category_id"))
            tipo_do_canal = dados_canal.get("type", "text")

            if canal is None:
                self._anotar(
                    f"➕ Criar canal `{dados_canal['name']}` ({tipo_do_canal})"
                )
                if not dry_run:
                    if "voice" in tipo_do_canal:
                        await guilda.create_voice_channel(
                            name=dados_canal["name"],
                            category=categoria,
                            reason="Restauração de backup",
                        )
                    else:
                        await guilda.create_text_channel(
                            name=dados_canal["name"],
                            category=categoria,
                            topic=dados_canal.get("topic"),
                            nsfw=dados_canal.get("nsfw", False),
                            reason="Restauração de backup",
                        )
                    await asyncio.sleep(ATRASO_RATE_LIMIT)
                continue

            nome_mudou = canal.name != dados_canal["name"]
            extras_mudaram = False
            if isinstance(canal, discord.TextChannel):
                extras_mudaram = canal.topic != dados_canal.get(
                    "topic"
                ) or canal.nsfw != dados_canal.get("nsfw", False)

            if nome_mudou or extras_mudaram:
                self._anotar(f"✏️ Atualizar canal `{canal.name}`")
                if not dry_run:
                    argumentos_da_edicao: dict = {
                        "name": dados_canal["name"],
                        "reason": "Restauração de backup",
                    }
                    if isinstance(canal, discord.TextChannel):
                        argumentos_da_edicao["topic"] = dados_canal.get("topic")
                        argumentos_da_edicao["nsfw"] = dados_canal.get("nsfw", False)
                    await canal.edit(**argumentos_da_edicao)
                    await asyncio.sleep(ATRASO_RATE_LIMIT)

        if not self.relatorio:
            self._anotar("Nenhuma alteração necessária nos canais.")
        return self.relatorio

    async def restaurar_membros(
        self,
        guilda: discord.Guild,
        backup: dict,
        dry_run: bool = False,
    ) -> list[str]:
        """Reaplica cargos e apelidos para quem ainda está no servidor."""
        self.relatorio = []
        cargos_por_id = {cargo.id: cargo for cargo in guilda.roles}
        cargos_por_nome = {
            cargo.name: cargo for cargo in guilda.roles if not cargo.is_default()
        }

        for dados_membro in backup.get("members", []):
            membro = guilda.get_member(dados_membro["id"])
            if membro is None:
                continue

            ids_desejados = set(dados_membro.get("role_ids") or [])
            nomes_desejados = dados_membro.get("role_names") or []

            cargos_alvo: list[discord.Role] = []
            for id_do_cargo in ids_desejados:
                cargo = cargos_por_id.get(id_do_cargo)
                if cargo and not cargo.is_default() and not cargo.managed:
                    cargos_alvo.append(cargo)

            for nome_do_cargo in nomes_desejados:
                cargo = cargos_por_nome.get(nome_do_cargo)
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

            cargos_para_adicionar = [
                cargo for cargo in cargos_alvo if cargo.id not in ids_atuais
            ]
            cargos_para_remover = [
                cargos_por_id[id_do_cargo]
                for id_do_cargo in ids_atuais - ids_alvo
                if id_do_cargo in cargos_por_id
                and not cargos_por_id[id_do_cargo].managed
            ]

            if cargos_para_adicionar or cargos_para_remover:
                nome_do_membro = dados_membro.get("name", membro.id)
                self._anotar(
                    f"👤 {nome_do_membro}: "
                    f"+{len(cargos_para_adicionar)} cargo(s), "
                    f"-{len(cargos_para_remover)} cargo(s)"
                )
                if not dry_run:
                    try:
                        if cargos_para_adicionar:
                            await membro.add_roles(
                                *cargos_para_adicionar,
                                reason="Restauração de backup",
                            )
                        if cargos_para_remover:
                            await membro.remove_roles(
                                *cargos_para_remover,
                                reason="Restauração de backup",
                            )
                    except discord.Forbidden:
                        self._anotar(
                            f"⚠️ Sem permissão para alterar cargos de {membro}."
                        )
                    await asyncio.sleep(ATRASO_RATE_LIMIT)

            apelido_no_backup = dados_membro.get("nickname")
            if apelido_no_backup != membro.nick:
                nome_do_membro = dados_membro.get("name", membro.id)
                self._anotar(
                    f"👤 {nome_do_membro}: "
                    f"apelido `{membro.nick}` → `{apelido_no_backup}`"
                )
                if not dry_run:
                    try:
                        await membro.edit(
                            nick=apelido_no_backup,
                            reason="Restauração de backup",
                        )
                    except discord.Forbidden:
                        self._anotar(f"⚠️ Sem permissão para renomear {membro}.")
                    await asyncio.sleep(ATRASO_RATE_LIMIT)

        if not self.relatorio:
            self._anotar("Nenhuma alteração necessária nos membros.")
        return self.relatorio

    async def restaurar_tudo(
        self,
        guilda: discord.Guild,
        backup: dict,
        dry_run: bool = False,
    ) -> dict[str, list[str]]:
        """Restaura cargos, categorias e canais (sem membros)."""
        return {
            "roles": await self.restaurar_cargos(guilda, backup, dry_run),
            "categories": await self.restaurar_categorias(guilda, backup, dry_run),
            "channels": await self.restaurar_canais(guilda, backup, dry_run),
        }
