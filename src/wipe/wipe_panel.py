"""
Painel efêmero de wipe — Components V2.

Aberto por /wipe (só administradores). Não é mensagem fixa no canal.
"""

from __future__ import annotations

import logging
import re

import discord

from src.config import CARGO_BASE_APOS_WIPE
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
)
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_info,
    responder_sucesso,
    responder_view,
)
from src.wipe.wipe_backup_service import montar_nome_da_temporada
from src.wipe.wipe_membros_service import (
    listar_preservados_e_comuns,
    nomes_cargos_preservados_do_membro,
)
from src.wipe.wipe_service import (
    executar_backup_banco_e_esvaziar,
    executar_backup_completo,
    executar_backup_discord,
    executar_limpar_cargos,
    executar_recriar_canais,
    executar_remover_cargos_escolhidos,
)
from src.wipe.wipe_state import (
    obter_estado_do_wipe,
    wipe_esta_em_andamento,
)

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


def _texto_fila_canais(ids_canais: list[int]) -> str:
    if not ids_canais:
        return "_Nenhum canal na fila._"
    return "\n".join(f"`•` `{id_canal}`" for id_canal in ids_canais)


# ---------------------------------------------------------------------------
# Painel principal (efêmero)
# ---------------------------------------------------------------------------


class PainelWipeLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel de controle do wipe (só na resposta efêmera do /wipe)."""

    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=600)
        self.guild = guild

        row1 = discord.ui.ActionRow()
        for rotulo, estilo, cid, callback in [
            ("Backup completo", discord.ButtonStyle.danger, "wipe:e:backup_c", self._ao_backup_completo),
            ("Backup Discord", discord.ButtonStyle.primary, "wipe:e:backup_d", self._ao_backup_discord),
            ("Backup banco + zerar", discord.ButtonStyle.danger, "wipe:e:backup_b", self._ao_backup_banco),
        ]:
            botao = discord.ui.Button(label=rotulo, style=estilo, custom_id=cid)
            botao.callback = callback
            row1.add_item(botao)

        row2 = discord.ui.ActionRow()
        for rotulo, estilo, cid, callback in [
            ("Limpar cargos (clássico)", discord.ButtonStyle.danger, "wipe:e:limpar", self._ao_limpar_classico),
            ("Selecionar cargos", discord.ButtonStyle.secondary, "wipe:e:sel_cargos", self._ao_selecionar_cargos),
            ("Recriar canais", discord.ButtonStyle.secondary, "wipe:e:canais", self._ao_recriar_canais),
        ]:
            botao = discord.ui.Button(label=rotulo, style=estilo, custom_id=cid)
            botao.callback = callback
            row2.add_item(botao)

        row3 = discord.ui.ActionRow()
        for rotulo, estilo, cid, callback in [
            ("Status", discord.ButtonStyle.primary, "wipe:e:status", self._ao_status),
            ("Ver preservados", discord.ButtonStyle.primary, "wipe:e:pres", self._ao_preservados),
        ]:
            botao = discord.ui.Button(label=rotulo, style=estilo, custom_id=cid)
            botao.callback = callback
            row3.add_item(botao)

        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# Painel de Wipe"),
            discord.ui.TextDisplay(
                "-# Efêmero — some quando fechar. Só administradores.\n"
                "-# Apelidos do servidor são apagados (fica só o username).\n"
                f"-# Clássico preserva diretoria/área + `{CARGO_BASE_APOS_WIPE}`."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            row1,
            row2,
            row3,
            accent_color=discord.Color.dark_gold(),
        )
        self.add_item(self.container)

    async def _ao_backup_completo(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao) or not await _exigir_livre(interacao):
            return
        await interacao.response.defer(ephemeral=True)
        try:
            estado = await executar_backup_completo(interacao.guild, interacao.user)
            await responder_sucesso(
                interacao,
                titulo="Backup completo",
                linhas=[
                    f"Discord: `{estado.caminho_backup_discord or '—'}`",
                    f"Banco: `{estado.caminho_backup_banco or '—'}`",
                    f"Tabelas: **{estado.tabelas_esvaziadas}**",
                ],
            )
        except Exception as erro:
            registrador.exception("[wipe] backup completo: %s", erro)
            await responder_erro(interacao, titulo="Backup falhou", linhas=[str(erro)])

    async def _ao_backup_discord(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao) or not await _exigir_livre(interacao):
            return
        await interacao.response.defer(ephemeral=True)
        try:
            estado = await executar_backup_discord(interacao.guild, interacao.user)
            await responder_sucesso(
                interacao,
                titulo="Backup Discord",
                linhas=[f"Arquivo: `{estado.caminho_backup_discord or '—'}`"],
            )
        except Exception as erro:
            registrador.exception("[wipe] backup discord: %s", erro)
            await responder_erro(
                interacao, titulo="Backup Discord falhou", linhas=[str(erro)]
            )

    async def _ao_backup_banco(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao) or not await _exigir_livre(interacao):
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
                ],
            )
        except Exception as erro:
            registrador.exception("[wipe] backup banco: %s", erro)
            await responder_erro(
                interacao, titulo="Backup banco falhou", linhas=[str(erro)]
            )

    async def _ao_limpar_classico(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao) or not await _exigir_livre(interacao):
            return
        preservados, comuns = listar_preservados_e_comuns(interacao.guild)
        await responder_view(
            interacao,
            ConfirmarLimpezaClassicaView(
                interacao.user.id, len(preservados), len(comuns)
            ),
            ephemeral=True,
        )

    async def _ao_selecionar_cargos(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao) or not await _exigir_livre(interacao):
            return
        await responder_view(
            interacao,
            SelecionarCargosParaLimparView(interacao.user.id),
            ephemeral=True,
        )

    async def _ao_recriar_canais(self, interacao: discord.Interaction) -> None:
        if not await _exigir_admin(interacao) or not await _exigir_livre(interacao):
            return
        await responder_view(
            interacao,
            MontarFilaCanaisView(interacao.user.id, []),
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
                    "Nenhuma operação neste processo.",
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
                f"Limpos: {estado.membros_limpos} | Falhas: {estado.membros_falha}",
                f"Tabelas: {estado.tabelas_esvaziadas}",
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


async def abrir_painel_wipe(interacao: discord.Interaction) -> None:
    """Envia o painel efêmero (resposta só para quem rodou /wipe)."""
    if interacao.guild is None:
        await responder_erro(
            interacao,
            titulo="Servidor necessário",
            linhas=["Use /wipe dentro do servidor."],
        )
        return
    await responder_view(
        interacao,
        PainelWipeLayout(guild=interacao.guild),
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# Limpeza clássica
# ---------------------------------------------------------------------------


class ConfirmarLimpezaClassicaView(LoggingViewMixin, discord.ui.LayoutView):
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
            label="Confirmar",
            style=discord.ButtonStyle.danger,
            custom_id="wipe:e:conf_limpar",
        )
        b_ok.callback = self._ao_confirmar
        row.add_item(b_ok)
        b_nao = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
            custom_id="wipe:e:canc_limpar",
        )
        b_nao.callback = self._ao_cancelar
        row.add_item(b_nao)
        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# Confirmar limpeza clássica"),
            discord.ui.TextDisplay(
                f"Preservados: **{quantidade_preservados}**\n"
                f"Comuns: **{quantidade_comuns}**\n"
                "Apelido do servidor removido (fica só o username)."
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
            interacao, titulo="Cancelado", linhas=["Nada foi alterado."]
        )

    async def _ao_confirmar(self, interacao: discord.Interaction) -> None:
        if not await _exigir_livre(interacao):
            return
        self.stop()
        await interacao.response.defer(ephemeral=True)
        try:
            estado = await executar_limpar_cargos(interacao.guild, interacao.user)
            await responder_sucesso(
                interacao,
                titulo="Limpeza concluída",
                linhas=[
                    f"Preservados: **{estado.membros_preservados}**",
                    f"Limpos: **{estado.membros_limpos}**",
                    f"Falhas: **{estado.membros_falha}**",
                ],
            )
        except Exception as erro:
            registrador.exception("[wipe] limpar: %s", erro)
            await responder_erro(
                interacao, titulo="Limpeza falhou", linhas=[str(erro)]
            )


# ---------------------------------------------------------------------------
# Select de cargos
# ---------------------------------------------------------------------------


class SelecionarCargosParaLimparView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, usuario_id: int):
        super().__init__(timeout=300)
        self.usuario_id = usuario_id
        self.select_cargos = discord.ui.RoleSelect(
            placeholder="Cargos para remover de todos",
            min_values=1,
            max_values=25,
            custom_id="wipe:e:rsel",
        )
        self.select_cargos.callback = self._ao_selecionar
        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# Selecionar cargos"),
            discord.ui.TextDisplay(
                "Remove os cargos escolhidos de **todos** os membros. "
                "Também apaga o apelido do servidor."
            ),
            discord.ui.ActionRow(self.select_cargos),
            accent_color=discord.Color.orange(),
        )
        self.add_item(self.container)

    async def interaction_check(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.usuario_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Só quem abriu este card pode usar."],
            )
            return False
        return True

    async def _ao_selecionar(self, interacao: discord.Interaction) -> None:
        if not await _exigir_livre(interacao):
            return
        roles = list(self.select_cargos.values or [])
        if not roles and interacao.guild is not None:
            for id_texto in (interacao.data or {}).get("values") or []:
                cargo = interacao.guild.get_role(int(id_texto))
                if cargo is not None:
                    roles.append(cargo)
        if not roles:
            await responder_erro(
                interacao, titulo="Nenhum cargo", linhas=["Selecione ao menos um."]
            )
            return
        nomes = ", ".join(cargo.name for cargo in roles)
        await responder_view(
            interacao,
            ConfirmarRemocaoCargosView(interacao.user.id, roles, nomes),
            ephemeral=True,
        )


class ConfirmarRemocaoCargosView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, usuario_id: int, cargos: list[discord.Role], nomes: str):
        super().__init__(timeout=300)
        self.usuario_id = usuario_id
        self.cargos = cargos
        row = discord.ui.ActionRow()
        b_ok = discord.ui.Button(
            label="Confirmar remoção",
            style=discord.ButtonStyle.danger,
            custom_id="wipe:e:conf_rc",
        )
        b_ok.callback = self._ao_confirmar
        row.add_item(b_ok)
        b_nao = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
            custom_id="wipe:e:canc_rc",
        )
        b_nao.callback = self._ao_cancelar
        row.add_item(b_nao)
        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# Confirmar remoção"),
            discord.ui.TextDisplay(f"Cargos: **{nomes}**"),
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
            interacao, titulo="Cancelado", linhas=["Nada foi alterado."]
        )

    async def _ao_confirmar(self, interacao: discord.Interaction) -> None:
        if not await _exigir_livre(interacao):
            return
        self.stop()
        await interacao.response.defer(ephemeral=True)
        try:
            estado = await executar_remover_cargos_escolhidos(
                interacao.guild, interacao.user, self.cargos
            )
            await responder_sucesso(
                interacao,
                titulo="Cargos removidos",
                linhas=[
                    f"Afetados: **{estado.membros_limpos}**",
                    f"Falhas: **{estado.membros_falha}**",
                ],
            )
        except Exception as erro:
            registrador.exception("[wipe] remover cargos: %s", erro)
            await responder_erro(
                interacao, titulo="Remoção falhou", linhas=[str(erro)]
            )


# ---------------------------------------------------------------------------
# Fila de canais por ID (adicionar / continuar)
# ---------------------------------------------------------------------------


class ModalIdCanal(LoggingModalMixin, discord.ui.Modal):
    """Pede um ou mais IDs de canal (separados por espaço, vírgula ou linha)."""

    def __init__(self, usuario_id: int, ids_atuais: list[int]):
        super().__init__(title="IDs dos canais")
        self.usuario_id = usuario_id
        self.ids_atuais = list(ids_atuais)
        self.campo_ids = discord.ui.TextInput(
            label="IDs (um ou vários)",
            placeholder="123456789012345678 987654321098765432",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
        )
        self.add_item(self.campo_ids)

    async def on_submit(self, interacao: discord.Interaction) -> None:
        texto = str(self.campo_ids.value or "")
        encontrados = [int(parte) for parte in re.findall(r"\d{15,20}", texto)]
        if not encontrados:
            await responder_erro(
                interacao,
                titulo="ID inválido",
                linhas=["Cole um ou mais IDs numéricos de canal."],
            )
            return
        unidos = list(self.ids_atuais)
        for id_canal in encontrados:
            if id_canal not in unidos:
                unidos.append(id_canal)
        await responder_view(
            interacao,
            MontarFilaCanaisView(self.usuario_id, unidos),
            ephemeral=True,
        )


class MontarFilaCanaisView(LoggingViewMixin, discord.ui.LayoutView):
    """Monta a lista de canais a recriar: adicionar mais ou executar."""

    def __init__(self, usuario_id: int, ids_canais: list[int]):
        super().__init__(timeout=600)
        self.usuario_id = usuario_id
        self.ids_canais = list(ids_canais)

        row = discord.ui.ActionRow()
        b_add = discord.ui.Button(
            label="Adicionar ID",
            style=discord.ButtonStyle.primary,
            custom_id="wipe:e:add_id",
        )
        b_add.callback = self._ao_adicionar
        row.add_item(b_add)
        b_sel = discord.ui.Button(
            label="Escolher no select",
            style=discord.ButtonStyle.secondary,
            custom_id="wipe:e:sel_ch",
        )
        b_sel.callback = self._ao_select
        row.add_item(b_sel)
        b_ok = discord.ui.Button(
            label="Continuar / confirmar",
            style=discord.ButtonStyle.danger,
            custom_id="wipe:e:go_ch",
            disabled=not bool(self.ids_canais),
        )
        b_ok.callback = self._ao_continuar
        row.add_item(b_ok)
        b_limpar = discord.ui.Button(
            label="Limpar fila",
            style=discord.ButtonStyle.secondary,
            custom_id="wipe:e:clr_ch",
        )
        b_limpar.callback = self._ao_limpar
        row.add_item(b_limpar)

        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# Recriar canais"),
            discord.ui.TextDisplay(
                "Duplica cada canal (mesmo nome e permissões) e **só então** "
                "apaga o original. Sem exceção de canal.\n\n"
                f"**Fila ({len(self.ids_canais)}):**\n"
                f"{_texto_fila_canais(self.ids_canais)}"
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
                linhas=["Só quem abriu este card pode usar."],
            )
            return False
        return True

    async def _ao_adicionar(self, interacao: discord.Interaction) -> None:
        await interacao.response.send_modal(
            ModalIdCanal(self.usuario_id, self.ids_canais)
        )

    async def _ao_select(self, interacao: discord.Interaction) -> None:
        await responder_view(
            interacao,
            SelectCanaisExtrasView(self.usuario_id, self.ids_canais),
            ephemeral=True,
        )

    async def _ao_limpar(self, interacao: discord.Interaction) -> None:
        await responder_view(
            interacao,
            MontarFilaCanaisView(self.usuario_id, []),
            ephemeral=True,
        )

    async def _ao_continuar(self, interacao: discord.Interaction) -> None:
        if not self.ids_canais:
            await responder_erro(
                interacao,
                titulo="Fila vazia",
                linhas=["Adicione ao menos um ID de canal."],
            )
            return
        await responder_view(
            interacao,
            ConfirmarRecriarCanaisView(self.usuario_id, self.ids_canais),
            ephemeral=True,
        )


class SelectCanaisExtrasView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, usuario_id: int, ids_atuais: list[int]):
        super().__init__(timeout=300)
        self.usuario_id = usuario_id
        self.ids_atuais = list(ids_atuais)
        self.select_canal = discord.ui.ChannelSelect(
            placeholder="Canais de texto (até 25)",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=25,
            custom_id="wipe:e:chsel",
        )
        self.select_canal.callback = self._ao_selecionar
        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# Escolher canais"),
            discord.ui.TextDisplay("Os escolhidos entram na fila."),
            discord.ui.ActionRow(self.select_canal),
            accent_color=discord.Color.orange(),
        )
        self.add_item(self.container)

    async def interaction_check(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.usuario_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Só quem abriu este card pode usar."],
            )
            return False
        return True

    async def _ao_selecionar(self, interacao: discord.Interaction) -> None:
        unidos = list(self.ids_atuais)
        for canal in self.select_canal.values or []:
            if canal.id not in unidos:
                unidos.append(canal.id)
        if not self.select_canal.values and interacao.guild is not None:
            for id_texto in (interacao.data or {}).get("values") or []:
                id_canal = int(id_texto)
                if id_canal not in unidos:
                    unidos.append(id_canal)
        await responder_view(
            interacao,
            MontarFilaCanaisView(self.usuario_id, unidos),
            ephemeral=True,
        )


class ConfirmarRecriarCanaisView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, usuario_id: int, ids_canais: list[int]):
        super().__init__(timeout=300)
        self.usuario_id = usuario_id
        self.ids_canais = list(ids_canais)
        row = discord.ui.ActionRow()
        b_ok = discord.ui.Button(
            label="Confirmar recriação",
            style=discord.ButtonStyle.danger,
            custom_id="wipe:e:conf_ch",
        )
        b_ok.callback = self._ao_confirmar
        row.add_item(b_ok)
        b_voltar = discord.ui.Button(
            label="Voltar à fila",
            style=discord.ButtonStyle.secondary,
            custom_id="wipe:e:back_ch",
        )
        b_voltar.callback = self._ao_voltar
        row.add_item(b_voltar)
        self.container = discord.ui.Container(
            discord.ui.TextDisplay("# Confirmar recriação"),
            discord.ui.TextDisplay(
                f"**{len(self.ids_canais)} canal(is)**\n"
                f"{_texto_fila_canais(self.ids_canais)}\n\n"
                "Cada um: duplicar → apagar original → relatório `NOME: ID`."
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

    async def _ao_voltar(self, interacao: discord.Interaction) -> None:
        await responder_view(
            interacao,
            MontarFilaCanaisView(self.usuario_id, self.ids_canais),
            ephemeral=True,
        )

    async def _ao_confirmar(self, interacao: discord.Interaction) -> None:
        if not await _exigir_livre(interacao):
            return
        self.stop()
        await interacao.response.defer(ephemeral=True)
        try:
            estado = await executar_recriar_canais(
                interacao.guild, interacao.user, self.ids_canais
            )
            # Últimas linhas do relatório = resultados NOME: ID
            resultados = [
                linha
                for linha in estado.linhas_do_relatorio
                if ": " in linha and not linha.startswith("Temporada")
            ][-40:]
            await responder_sucesso(
                interacao,
                titulo="Canais recriados",
                linhas=resultados or ["Sem linhas de resultado."],
            )
        except Exception as erro:
            registrador.exception("[wipe] recriar canais: %s", erro)
            await responder_erro(
                interacao, titulo="Recriação falhou", linhas=[str(erro)]
            )
