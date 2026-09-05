"""
Painel persistente de wipe — Components V2.

Todas as ações de temporada ficam em botões neste painel.
Não há comandos de barra: o controle é só por aqui.
"""

from __future__ import annotations

import logging

import discord

from src.config import CARGO_BASE_APOS_WIPE
from src.utils.error_handling import LoggingViewMixin
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_info,
    responder_sucesso,
    responder_view,
)
from src.wipe.wipe_membros_service import (
    listar_preservados_e_comuns,
    nomes_cargos_preservados_do_membro,
)
from src.wipe.wipe_recuperacao_service import executar_recuperacao_no_ready
from src.wipe.wipe_service import (
    executar_backup_banco_e_esvaziar,
    executar_backup_completo,
    executar_backup_discord,
    executar_limpar_cargos,
    executar_recriar_canal,
    executar_remover_cargos_escolhidos,
)
from src.wipe.wipe_state import (
    obter_estado_do_wipe,
    wipe_esta_em_andamento,
)
from src.wipe.wipe_backup_service import montar_nome_da_temporada

registrador = logging.getLogger(__name__)


def _membro_e_administrador(membro: discord.Member) -> bool:
    return membro.guild_permissions.administrator


async def _exigir_admin(interacao: discord.Interaction) -> bool:
    if not isinstance(interacao.user, discord.Member):
        await responder_erro(
            interacao,
            titulo="Servidor necessário",
            linhas=["Use este painel dentro do servidor."],
        )
        return False
    if not _membro_e_administrador(interacao.user):
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=["Só administradores podem usar o painel de wipe."],
        )
        return False
    return True


async def _exigir_livre(interacao: discord.Interaction) -> bool:
    if wipe_esta_em_andamento():
        await responder_erro(
            interacao,
            titulo="Wipe em andamento",
            linhas=["Já existe uma operação rodando. Aguarde terminar."],
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Painel principal (persistente)
# ---------------------------------------------------------------------------


class PainelWipeLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel fixo com todas as ações de wipe."""

    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

        icone = guild.icon.url if guild.icon else None

        row1 = discord.ui.ActionRow()
        b_backup = discord.ui.Button(
            label="Backup completo",
            style=discord.ButtonStyle.danger,
            custom_id="wipe:backup_completo",
        )
        b_backup.callback = self._ao_backup_completo
        row1.add_item(b_backup)

        b_discord = discord.ui.Button(
            label="Backup Discord",
            style=discord.ButtonStyle.primary,
            custom_id="wipe:backup_discord",
        )
        b_discord.callback = self._ao_backup_discord
        row1.add_item(b_discord)

        b_banco = discord.ui.Button(
            label="Backup banco + zerar",
            style=discord.ButtonStyle.danger,
            custom_id="wipe:backup_banco",
        )
        b_banco.callback = self._ao_backup_banco
        row1.add_item(b_banco)

        row2 = discord.ui.ActionRow()
        b_limpar = discord.ui.Button(
            label="Limpar cargos (clássico)",
            style=discord.ButtonStyle.danger,
            custom_id="wipe:limpar_classico",
        )
        b_limpar.callback = self._ao_limpar_classico
        row2.add_item(b_limpar)

        b_selecionar = discord.ui.Button(
            label="Selecionar cargos p/ limpar",
            style=discord.ButtonStyle.secondary,
            custom_id="wipe:selecionar_cargos",
        )
        b_selecionar.callback = self._ao_selecionar_cargos
        row2.add_item(b_selecionar)

        b_canal = discord.ui.Button(
            label="Recriar canal",
            style=discord.ButtonStyle.secondary,
            custom_id="wipe:recriar_canal",
        )
        b_canal.callback = self._ao_recriar_canal
        row2.add_item(b_canal)

        row3 = discord.ui.ActionRow()
        b_status = discord.ui.Button(
            label="Status",
            style=discord.ButtonStyle.primary,
            custom_id="wipe:status",
        )
        b_status.callback = self._ao_status
        row3.add_item(b_status)

        b_diretoria = discord.ui.Button(
            label="Ver preservados",
            style=discord.ButtonStyle.primary,
            custom_id="wipe:preservados",
        )
        b_diretoria.callback = self._ao_preservados
        row3.add_item(b_diretoria)

        b_recupera = discord.ui.Button(
            label="Recuperar responsável",
            style=discord.ButtonStyle.success,
            custom_id="wipe:recuperar",
        )
        b_recupera.callback = self._ao_recuperar
        row3.add_item(b_recupera)

        secao = discord.ui.Section(
            "# Painel de Wipe",
            (
                "-# Controle total da virada de temporada.\n"
                "-# Backup, banco, cargos, canais e recuperação.\n"
                "-# Só administradores. Tudo é registrado em LOGS_WIPE."
            ),
            accessory=discord.ui.Thumbnail(icone) if icone else None,
        )

        self.container = discord.ui.Container(
            secao,
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                "## Ações disponíveis\n"
                "### Backup\n"
                "`•` Completo = Discord + banco + esvaziar tabelas\n"
                "`•` Discord = só snapshot de cargos/canais/membros\n"
                "`•` Banco + zerar = dump e TRUNCATE das tabelas\n"
                "### Cargos e canais\n"
                "`•` Clássico = preserva diretoria/área + "
                f"`{CARGO_BASE_APOS_WIPE}`\n"
                "`•` Selecionar = escolhe quais cargos tirar de todo mundo\n"
                "`•` Recriar canal = apaga e cria de novo (responde NOME: ID)"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row1,
            row2,
            row3,
            accent_color=discord.Color.dark_gold(),
        )
        self.add_item(self.container)

    async def _ao_backup_completo(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao):
            return
        if not await _exigir_livre(interacao):
            return
        await interacao.response.defer(ephemeral=True)
        try:
            estado = await executar_backup_completo(
                interacao.guild, interacao.user
            )
            await responder_sucesso(
                interacao,
                titulo="Backup completo",
                linhas=[
                    f"Temporada: `{estado.temporada}`",
                    f"Discord: `{estado.caminho_backup_discord or '—'}`",
                    f"Banco: `{estado.caminho_backup_banco or '—'}`",
                    f"Tabelas: **{estado.tabelas_esvaziadas}**",
                    "Relatório no canal de logs do wipe.",
                ],
            )
        except Exception as erro:
            registrador.exception("[wipe] backup completo: %s", erro)
            await responder_erro(
                interacao,
                titulo="Backup falhou",
                linhas=[str(erro)],
            )

    async def _ao_backup_discord(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao):
            return
        if not await _exigir_livre(interacao):
            return
        await interacao.response.defer(ephemeral=True)
        try:
            estado = await executar_backup_discord(
                interacao.guild, interacao.user
            )
            await responder_sucesso(
                interacao,
                titulo="Backup Discord",
                linhas=[
                    f"Arquivo: `{estado.caminho_backup_discord or '—'}`",
                    "Relatório no canal de logs do wipe.",
                ],
            )
        except Exception as erro:
            registrador.exception("[wipe] backup discord: %s", erro)
            await responder_erro(
                interacao,
                titulo="Backup Discord falhou",
                linhas=[str(erro)],
            )

    async def _ao_backup_banco(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao):
            return
        if not await _exigir_livre(interacao):
            return
        await interacao.response.defer(ephemeral=True)
        try:
            estado = await executar_backup_banco_e_esvaziar(
                interacao.guild, interacao.user
            )
            await responder_sucesso(
                interacao,
                titulo="Backup banco + tabelas zeradas",
                linhas=[
                    f"Arquivo: `{estado.caminho_backup_banco or '—'}`",
                    f"Tabelas: **{estado.tabelas_esvaziadas}**",
                    "Relatório no canal de logs do wipe.",
                ],
            )
        except Exception as erro:
            registrador.exception("[wipe] backup banco: %s", erro)
            await responder_erro(
                interacao,
                titulo="Backup banco falhou",
                linhas=[str(erro)],
            )

    async def _ao_limpar_classico(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao):
            return
        if not await _exigir_livre(interacao):
            return
        preservados, comuns = listar_preservados_e_comuns(interacao.guild)
        await responder_view(
            interacao,
            ConfirmarLimpezaClassicaView(
                interacao.user.id,
                len(preservados),
                len(comuns),
            ),
            ephemeral=True,
        )

    async def _ao_selecionar_cargos(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao):
            return
        if not await _exigir_livre(interacao):
            return
        await responder_view(
            interacao,
            SelecionarCargosParaLimparView(interacao.user.id),
            ephemeral=True,
        )

    async def _ao_recriar_canal(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao):
            return
        if not await _exigir_livre(interacao):
            return
        await responder_view(
            interacao,
            SelecionarCanalParaRecriarView(interacao.user.id),
            ephemeral=True,
        )

    async def _ao_status(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao):
            return
        estado = obter_estado_do_wipe()
        if estado is None:
            await responder_info(
                interacao,
                titulo="Status do wipe",
                linhas=[
                    "Nenhuma operação neste processo do bot.",
                    f"Temporada sugerida: `{montar_nome_da_temporada()}`",
                ],
            )
            return
        andamento = "sim" if estado.em_andamento else "não"
        await responder_info(
            interacao,
            titulo="Status do wipe",
            linhas=[
                f"Temporada: `{estado.temporada}`",
                f"Em andamento: **{andamento}**",
                f"Fase: `{estado.fase}`",
                f"Iniciador: {estado.iniciador_nome}",
                f"Preservados: {estado.membros_preservados}",
                f"Limpos: {estado.membros_limpos}",
                f"Falhas: {estado.membros_falha}",
                f"Tabelas: {estado.tabelas_esvaziadas}",
                f"Discord: `{estado.caminho_backup_discord or '—'}`",
                f"Banco: `{estado.caminho_backup_banco or '—'}`",
            ],
        )

    async def _ao_preservados(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao):
            return
        preservados, comuns = listar_preservados_e_comuns(interacao.guild)
        nomes = []
        for membro in preservados[:40]:
            cargos = nomes_cargos_preservados_do_membro(membro)
            nomes.append(
                f"• {membro} (`{membro.id}`) — {', '.join(cargos) or 'admin/id'}"
            )
        await responder_info(
            interacao,
            titulo="Preservados no limpar clássico",
            linhas=[
                f"Preservados: **{len(preservados)}**",
                f"Comuns: **{len(comuns)}**",
                "",
                *nomes,
            ],
        )

    async def _ao_recuperar(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao):
            return
        await interacao.response.defer(ephemeral=True)
        try:
            linhas = await executar_recuperacao_no_ready(interacao.guild)
            await responder_sucesso(
                interacao,
                titulo="Recuperação do responsável",
                linhas=linhas or ["Nada a fazer."],
            )
        except Exception as erro:
            registrador.exception("[wipe] recuperar: %s", erro)
            await responder_erro(
                interacao,
                titulo="Recuperação falhou",
                linhas=[str(erro)],
            )


# ---------------------------------------------------------------------------
# Confirmação limpeza clássica
# ---------------------------------------------------------------------------


class ConfirmarLimpezaClassicaView(LoggingViewMixin, discord.ui.LayoutView):
    """Confirma antes do limpar clássico."""

    def __init__(
        self,
        usuario_id: int,
        quantidade_preservados: int,
        quantidade_comuns: int,
    ):
        super().__init__(timeout=300)
        self.usuario_id = usuario_id

        row = discord.ui.ActionRow()
        b_ok = discord.ui.Button(
            label="Confirmar limpeza",
            style=discord.ButtonStyle.danger,
            custom_id="wipe:confirmar_classico",
        )
        b_ok.callback = self._ao_confirmar
        row.add_item(b_ok)
        b_nao = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
            custom_id="wipe:cancelar_classico",
        )
        b_nao.callback = self._ao_cancelar
        row.add_item(b_nao)

        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# Confirmar limpeza clássica"),
            discord.ui.TextDisplay(
                f"Preservados: **{quantidade_preservados}**\n"
                f"Comuns (perdem cargos): **{quantidade_comuns}**\n"
                "Prefixo removido de todo mundo."
            ),
            row,
            accent_color=discord.Color.dark_red(),
        )
        self.add_item(self.container)

    async def interaction_check(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.usuario_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Só quem abriu este card pode confirmar."],
            )
            return False
        return True

    async def _ao_cancelar(self, interacao: discord.Interaction) -> None:
        self.stop()
        await responder_aviso(
            interacao,
            titulo="Cancelado",
            linhas=["Nenhum cargo foi alterado."],
        )

    async def _ao_confirmar(self, interacao: discord.Interaction) -> None:
        if not await _exigir_livre(interacao):
            return
        self.stop()
        await interacao.response.defer(ephemeral=True)
        try:
            estado = await executar_limpar_cargos(
                interacao.guild, interacao.user
            )
            await responder_sucesso(
                interacao,
                titulo="Limpeza clássica concluída",
                linhas=[
                    f"Preservados: **{estado.membros_preservados}**",
                    f"Limpos: **{estado.membros_limpos}**",
                    f"Falhas: **{estado.membros_falha}**",
                ],
            )
        except Exception as erro:
            registrador.exception("[wipe] limpar classico: %s", erro)
            await responder_erro(
                interacao,
                titulo="Limpeza falhou",
                linhas=[str(erro)],
            )


# ---------------------------------------------------------------------------
# Select de cargos
# ---------------------------------------------------------------------------


class SelecionarCargosParaLimparView(LoggingViewMixin, discord.ui.LayoutView):
    """Escolhe quais cargos tirar de todos os membros."""

    def __init__(self, usuario_id: int):
        super().__init__(timeout=300)
        self.usuario_id = usuario_id

        self.select_cargos = discord.ui.RoleSelect(
            placeholder="Selecione os cargos para remover de todos",
            min_values=1,
            max_values=25,
            custom_id="wipe:select_cargos",
        )
        self.select_cargos.callback = self._ao_selecionar
        row = discord.ui.ActionRow(self.select_cargos)

        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# Selecionar cargos para limpar"),
            discord.ui.TextDisplay(
                "Os cargos escolhidos serão removidos de **todos** os "
                "membros que os tiverem. Prefixo do nick também é limpo.\n"
                "Cargos managed (bots) são ignorados."
            ),
            row,
            accent_color=discord.Color.orange(),
        )
        self.add_item(self.container)

    async def interaction_check(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.usuario_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Só quem abriu este card pode usar o select."],
            )
            return False
        return True

    async def _ao_selecionar(self, interacao: discord.Interaction) -> None:
        if not await _exigir_livre(interacao):
            return
        roles_selecionados: list[discord.Role] = list(
            self.select_cargos.values or []
        )
        if not roles_selecionados and interacao.guild is not None:
            dados = interacao.data or {}
            ids = dados.get("values") or []
            for id_texto in ids:
                cargo = interacao.guild.get_role(int(id_texto))
                if cargo is not None:
                    roles_selecionados.append(cargo)

        if not roles_selecionados:
            await responder_erro(
                interacao,
                titulo="Nenhum cargo",
                linhas=["Selecione ao menos um cargo."],
            )
            return

        nomes = ", ".join(cargo.name for cargo in roles_selecionados)
        await responder_view(
            interacao,
            ConfirmarRemocaoCargosView(
                interacao.user.id,
                roles_selecionados,
                nomes,
            ),
            ephemeral=True,
        )


class ConfirmarRemocaoCargosView(LoggingViewMixin, discord.ui.LayoutView):
    """Confirma remoção dos cargos selecionados."""

    def __init__(
        self,
        usuario_id: int,
        cargos: list[discord.Role],
        nomes: str,
    ):
        super().__init__(timeout=300)
        self.usuario_id = usuario_id
        self.cargos = cargos

        row = discord.ui.ActionRow()
        b_ok = discord.ui.Button(
            label="Confirmar remoção",
            style=discord.ButtonStyle.danger,
            custom_id="wipe:confirmar_cargos_select",
        )
        b_ok.callback = self._ao_confirmar
        row.add_item(b_ok)
        b_nao = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
            custom_id="wipe:cancelar_cargos_select",
        )
        b_nao.callback = self._ao_cancelar
        row.add_item(b_nao)

        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# Confirmar remoção de cargos"),
            discord.ui.TextDisplay(
                f"Cargos: **{nomes}**\n"
                "Serão removidos de todos os membros que os tiverem."
            ),
            row,
            accent_color=discord.Color.dark_red(),
        )
        self.add_item(self.container)

    async def interaction_check(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.usuario_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Só quem abriu este card pode confirmar."],
            )
            return False
        return True

    async def _ao_cancelar(self, interacao: discord.Interaction) -> None:
        self.stop()
        await responder_aviso(
            interacao,
            titulo="Cancelado",
            linhas=["Nenhum cargo foi alterado."],
        )

    async def _ao_confirmar(self, interacao: discord.Interaction) -> None:
        if not await _exigir_livre(interacao):
            return
        self.stop()
        await interacao.response.defer(ephemeral=True)
        try:
            estado = await executar_remover_cargos_escolhidos(
                interacao.guild,
                interacao.user,
                self.cargos,
            )
            await responder_sucesso(
                interacao,
                titulo="Cargos removidos",
                linhas=[
                    f"Membros afetados: **{estado.membros_limpos}**",
                    f"Falhas: **{estado.membros_falha}**",
                    "Relatório no canal de logs do wipe.",
                ],
            )
        except Exception as erro:
            registrador.exception("[wipe] remover cargos select: %s", erro)
            await responder_erro(
                interacao,
                titulo="Remoção falhou",
                linhas=[str(erro)],
            )


# ---------------------------------------------------------------------------
# Select de canal
# ---------------------------------------------------------------------------


class SelecionarCanalParaRecriarView(LoggingViewMixin, discord.ui.LayoutView):
    """Escolhe um canal de texto para apagar e recriar."""

    def __init__(self, usuario_id: int):
        super().__init__(timeout=300)
        self.usuario_id = usuario_id

        self.select_canal = discord.ui.ChannelSelect(
            placeholder="Selecione o canal de texto",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            custom_id="wipe:select_canal",
        )
        self.select_canal.callback = self._ao_selecionar
        row = discord.ui.ActionRow(self.select_canal)

        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# Recriar canal"),
            discord.ui.TextDisplay(
                "O canal será **apagado e recriado** com as mesmas "
                "permissões, categoria e posição.\n"
                "O histórico some. A resposta traz **NOME: ID** do canal novo."
            ),
            row,
            accent_color=discord.Color.orange(),
        )
        self.add_item(self.container)

    async def interaction_check(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.usuario_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Só quem abriu este card pode usar o select."],
            )
            return False
        return True

    async def _ao_selecionar(self, interacao: discord.Interaction) -> None:
        if not await _exigir_livre(interacao):
            return
        canal = None
        if self.select_canal.values:
            canal = self.select_canal.values[0]
        if canal is None and interacao.guild is not None:
            dados = interacao.data or {}
            ids = dados.get("values") or []
            if ids:
                canal = interacao.guild.get_channel(int(ids[0]))

        if canal is None or not isinstance(canal, discord.TextChannel):
            await responder_erro(
                interacao,
                titulo="Canal inválido",
                linhas=["Escolha um canal de texto válido."],
            )
            return

        await responder_view(
            interacao,
            ConfirmarRecriarCanalView(interacao.user.id, canal),
            ephemeral=True,
        )


class ConfirmarRecriarCanalView(LoggingViewMixin, discord.ui.LayoutView):
    """Confirma recriação do canal."""

    def __init__(self, usuario_id: int, canal: discord.TextChannel):
        super().__init__(timeout=300)
        self.usuario_id = usuario_id
        self.canal_id = canal.id
        self.canal_nome = canal.name

        row = discord.ui.ActionRow()
        b_ok = discord.ui.Button(
            label="Confirmar recriação",
            style=discord.ButtonStyle.danger,
            custom_id="wipe:confirmar_canal",
        )
        b_ok.callback = self._ao_confirmar
        row.add_item(b_ok)
        b_nao = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
            custom_id="wipe:cancelar_canal",
        )
        b_nao.callback = self._ao_cancelar
        row.add_item(b_nao)

        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# Confirmar recriação de canal"),
            discord.ui.TextDisplay(
                f"Canal: **#{self.canal_nome}** (`{self.canal_id}`)\n"
                "O histórico será apagado. O ID novo será informado."
            ),
            row,
            accent_color=discord.Color.dark_red(),
        )
        self.add_item(self.container)

    async def interaction_check(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.usuario_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Só quem abriu este card pode confirmar."],
            )
            return False
        return True

    async def _ao_cancelar(self, interacao: discord.Interaction) -> None:
        self.stop()
        await responder_aviso(
            interacao,
            titulo="Cancelado",
            linhas=["O canal não foi alterado."],
        )

    async def _ao_confirmar(self, interacao: discord.Interaction) -> None:
        if not await _exigir_livre(interacao):
            return
        if interacao.guild is None:
            await responder_erro(
                interacao,
                titulo="Servidor necessário",
                linhas=["Use dentro do servidor."],
            )
            return
        self.stop()
        await interacao.response.defer(ephemeral=True)
        canal = interacao.guild.get_channel(self.canal_id)
        if canal is None or not isinstance(canal, discord.TextChannel):
            await responder_erro(
                interacao,
                titulo="Canal sumiu",
                linhas=[f"Não encontrei o canal `{self.canal_id}`."],
            )
            return
        try:
            linha, _estado = await executar_recriar_canal(
                interacao.guild, interacao.user, canal
            )
            await responder_sucesso(
                interacao,
                titulo="Canal recriado",
                linhas=[
                    f"**{linha}**",
                    "Formato: NOME: ID",
                    "Relatório no canal de logs do wipe.",
                ],
            )
        except Exception as erro:
            registrador.exception("[wipe] recriar canal: %s", erro)
            await responder_erro(
                interacao,
                titulo="Recriação falhou",
                linhas=[str(erro)],
            )
