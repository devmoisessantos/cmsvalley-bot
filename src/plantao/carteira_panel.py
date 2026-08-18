# src/plantao/carteira_panel.py
"""Painéis ephemeral da carteira de moedas (Components V2)."""

from __future__ import annotations

import discord

from src.config import (
    CANAIS,
    VALOR_MOEDA_INGAME,
)
from src.plantao.carteira_service import (
    aprovar_deposito,
    cargo_principal_hierarquia,
    criar_pedido_deposito,
    equivalente_em_reais,
    listar_extrato,
    membro_na_hierarquia,
    obter_saldo,
    recusar_deposito,
    rotulo_tipo_movimentacao,
    transferir_moedas,
)
from src.recrutamento.recrutamento_service import resolver_id_fivem
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
    capturar_erro_e_logar,
    ignorar_falha_cosmetica,
)
from src.utils.formatacao import (
    formatar_dinheiro,
    para_horario_brasilia,
)
from src.utils.mensagens import (
    COR_AVISO,
    COR_ERRO,
    COR_SUCESSO,
    editar_mensagem_original,
    responder_aviso,
    responder_erro,
    responder_sucesso,
    responder_view,
)
from src.utils.notificacao import enviar_dm_card

CUSTOM_ID_DEP_APROVAR = "carteira:dep_ok:"
CUSTOM_ID_DEP_RECUSAR = "carteira:dep_no:"


async def _fid(membro: discord.Member) -> str:
    return (await resolver_id_fivem(membro.id)) or "—"


def _membro_desde(membro: discord.Member) -> str:
    entrada = membro.joined_at
    if entrada is None:
        return "—"
    local = para_horario_brasilia(entrada)
    if local is None:
        return "—"
    meses = (
        "Jan",
        "Fev",
        "Mar",
        "Abr",
        "Mai",
        "Jun",
        "Jul",
        "Ago",
        "Set",
        "Out",
        "Nov",
        "Dez",
    )
    return f"{local.day} de {meses[local.month - 1]} de {local.year}"


class CarteiraHubView(LoggingViewMixin, discord.ui.LayoutView):
    """Card resumo da carteira + ações."""

    def __init__(
        self,
        membro: discord.Member,
        *,
        saldo: int,
        id_fivem: str,
    ):
        super().__init__(timeout=300)
        self.membro_id = membro.id
        self.saldo = saldo

        corpo = (
            f"> `👤` * **Membro:** {membro.mention} | **FID:** `{id_fivem}`\n"
            f"> `🪙` * **Saldo:** `{saldo} moedas`\n"
            f"> `💵` * **Equivalente:** `{equivalente_em_reais(saldo)}`\n"
            f"> `⏱️` * **Ganho:** `1 moeda / 30 min` em call de plantão\n\n"
            "## Ações\n"
            "Use os botões abaixo para movimentar suas moedas."
        )
        linha = discord.ui.ActionRow()
        for label, emoji, style, funcao_ao_clicar, cid in (
            (
                "Transferir",
                "💸",
                discord.ButtonStyle.primary,
                self._ao_transferir,
                "carteira:hub_transferir",
            ),
            (
                "Depositar",
                "📥",
                discord.ButtonStyle.secondary,
                self._ao_depositar,
                "carteira:hub_depositar",
            ),
            (
                "Extrato",
                "📜",
                discord.ButtonStyle.secondary,
                self._ao_extrato,
                "carteira:hub_extrato",
            ),
            (
                "Trocar moedas",
                "💵",
                discord.ButtonStyle.success,
                self._ao_trocar,
                "carteira:hub_trocar",
            ),
        ):
            botao = discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=cid,
            )
            botao.callback = funcao_ao_clicar
            linha.add_item(botao)

        self.add_item(
            discord.ui.Container(
                discord.ui.Section(
                    "# 💰 Carteira — CMS Valley",
                    corpo,
                    accessory=discord.ui.Thumbnail(membro.display_avatar.url),
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha,
                discord.ui.TextDisplay(
                    f"-# {membro.guild.name if membro.guild else 'CMS Valley'} "
                    "• carteira pessoal"
                ),
                accent_color=discord.Color.dark_teal(),
            )
        )

    def _autor_ok(self, interacao: discord.Interaction) -> bool:
        return interacao.user.id == self.membro_id

    async def _ao_transferir(self, interacao: discord.Interaction):
        if not self._autor_ok(interacao):
            await responder_erro(
                interacao,
                titulo="Não é a sua carteira",
                linhas=["Só quem abriu o painel pode usar os botões."],
            )
            return
        if self.saldo <= 0:
            await responder_aviso(
                interacao,
                titulo="Sem moedas",
                linhas=["Você não tem saldo para transferir."],
            )
            return
        await responder_view(
            interacao,
            ViewSelecionarDestinoTransferencia(
                remetente_id=self.membro_id,
                saldo=self.saldo,
            ),
            ephemeral=True,
        )

    async def _ao_depositar(self, interacao: discord.Interaction):
        if not self._autor_ok(interacao):
            await responder_erro(
                interacao,
                titulo="Não é a sua carteira",
                linhas=["Só quem abriu o painel pode usar os botões."],
            )
            return
        await responder_view(
            interacao,
            ViewIntroDeposito(
                membro_id=self.membro_id,
                saldo=self.saldo,
            ),
            ephemeral=True,
        )

    async def _ao_extrato(self, interacao: discord.Interaction):
        if not self._autor_ok(interacao):
            await responder_erro(
                interacao,
                titulo="Não é a sua carteira",
                linhas=["Só quem abriu o painel pode usar os botões."],
            )
            return
        await interacao.response.defer(ephemeral=True)
        movimentos = await listar_extrato(self.membro_id, limite=15)
        saldo = await obter_saldo(self.membro_id)
        linhas_mov: list[str] = []
        for mov in movimentos:
            momento = para_horario_brasilia(mov.criado_em)
            marca = momento.strftime("%d/%m %H:%M") if momento else "—"
            sinal = f"+{mov.valor}" if mov.valor > 0 else str(mov.valor)
            extra = ""
            if mov.outro_discord_id:
                extra = f" · <@{mov.outro_discord_id}>"
            linhas_mov.append(
                f"> `{marca}` · `{sinal}` · {rotulo_tipo_movimentacao(mov.tipo)}{extra}"
            )
        if not linhas_mov:
            linhas_mov = ["> _Nenhuma movimentação ainda._"]

        corpo = (
            f"> `💰` * **Saldo atual:** `{saldo} moedas` · "
            f"`{equivalente_em_reais(saldo)}`\n\n"
            "## Últimas movimentações\n"
            + "\n".join(linhas_mov)
            + "\n\n-# Mostrando as 15 mais recentes"
        )
        view = discord.ui.LayoutView(timeout=180)
        view.add_item(
            discord.ui.Container(
                discord.ui.Section(
                    "# 📜 Extrato de moedas",
                    corpo,
                    accessory=discord.ui.Thumbnail(interacao.user.display_avatar.url),
                ),
                accent_color=discord.Color.blurple(),
            )
        )
        await responder_view(
            interacao,
            view,
            ephemeral=True,
        )

    async def _ao_trocar(self, interacao: discord.Interaction):
        if not self._autor_ok(interacao):
            await responder_erro(
                interacao,
                titulo="Não é a sua carteira",
                linhas=["Só quem abriu o painel pode usar os botões."],
            )
            return
        if self.saldo <= 0:
            await responder_aviso(
                interacao,
                titulo="Sem moedas",
                linhas=["Você não tem moedas para trocar."],
            )
            return
        from src.plantao.plantao_panel import ModalTrocarMoedasPlantao

        await interacao.response.send_modal(
            ModalTrocarMoedasPlantao(saldo_disponivel=self.saldo)
        )


async def abrir_carteira(interacao: discord.Interaction) -> None:
    """Abre o hub da carteira (ephemeral)."""
    membro = interacao.user
    if not isinstance(membro, discord.Member):
        await responder_erro(
            interacao,
            titulo="Contexto inválido",
            linhas=["Use dentro do servidor."],
        )
        return
    if not membro_na_hierarquia(membro):
        await responder_aviso(
            interacao,
            titulo="Hierarquia necessária",
            linhas=[
                "A carteira é exclusiva para membros da **hierarquia** hospitalar.",
            ],
        )
        return
    saldo = await obter_saldo(membro.id)
    id_fivem = await _fid(membro)
    view = CarteiraHubView(membro, saldo=saldo, id_fivem=id_fivem)
    if interacao.response.is_done():
        await responder_view(
            interacao,
            view,
            ephemeral=True,
        )
    else:
        await responder_view(
            interacao,
            view,
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Transferência
# ---------------------------------------------------------------------------


class ViewSelecionarDestinoTransferencia(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, *, remetente_id: int, saldo: int):
        super().__init__(timeout=180)
        self.remetente_id = remetente_id
        self.saldo = saldo

        seletor = discord.ui.UserSelect(
            placeholder="Selecione o destinatário…",
            min_values=1,
            max_values=1,
            custom_id="carteira:select_destino",
        )
        seletor.callback = self._ao_escolher
        linha = discord.ui.ActionRow()
        linha.add_item(seletor)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# 💸 Transferir moedas\n"
                    "Escolha um **membro da hierarquia** para receber as moedas."
                ),
                linha,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _ao_escolher(self, interacao: discord.Interaction):
        if interacao.user.id != self.remetente_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Este seletor não é seu."],
            )
            return
        valores = (interacao.data or {}).get("values") or []
        if not valores:
            await responder_erro(
                interacao,
                titulo="Seleção vazia",
                linhas=["Nenhum destinatário selecionado."],
            )
            return
        guilda = interacao.guild
        if guilda is None:
            await responder_erro(
                interacao,
                titulo="Servidor ausente",
                linhas=["Use dentro do servidor."],
            )
            return
        destino_id = int(valores[0])
        destino = guilda.get_member(destino_id)
        if destino is None:
            try:
                destino = await guilda.fetch_member(destino_id)
            except discord.HTTPException:
                destino = None
        if destino is None:
            await responder_erro(
                interacao,
                titulo="Membro não encontrado",
                linhas=[f"ID `{destino_id}` não está no servidor."],
            )
            return
        if destino.id == self.remetente_id:
            await responder_aviso(
                interacao,
                titulo="Destino inválido",
                linhas=["Você não pode transferir para si mesmo."],
            )
            return
        if destino.bot:
            await responder_aviso(
                interacao,
                titulo="Destino inválido",
                linhas=["Não é possível transferir para bots."],
            )
            return
        if not membro_na_hierarquia(destino):
            await responder_aviso(
                interacao,
                titulo="Fora da hierarquia",
                linhas=[
                    f"{destino.mention} não faz parte da hierarquia hospitalar.",
                ],
            )
            return

        fid = await _fid(destino)
        cargo = cargo_principal_hierarquia(destino)
        corpo = (
            "## Destinatário\n"
            f"> `👤` * **Nome:** {destino.mention}\n"
            f"> `🪪` * **FID:** `{fid}`\n"
            f"> `🏷️` * **Cargo:** `{cargo}`\n"
            f"> `📅` * **Membro desde:** `{_membro_desde(destino)}`\n\n"
            "## Sua carteira\n"
            f"> `🪙` * **Saldo atual:** `{self.saldo} moedas`\n"
            f"> `💵` * **Equivalente:** `{equivalente_em_reais(self.saldo)}`\n\n"
            "Confira se é a pessoa certa antes de continuar."
        )
        view = ViewConfirmarDestinoTransferencia(
            remetente_id=self.remetente_id,
            destino_id=destino.id,
            saldo=self.saldo,
            corpo=corpo,
            url_thumb=destino.display_avatar.url,
            fid_destino=fid,
            cargo_destino=cargo,
        )
        await editar_mensagem_original(
            interacao,
            view=view,
        )


class ViewConfirmarDestinoTransferencia(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        *,
        remetente_id: int,
        destino_id: int,
        saldo: int,
        corpo: str,
        url_thumb: str,
        fid_destino: str,
        cargo_destino: str,
    ):
        super().__init__(timeout=180)
        self.remetente_id = remetente_id
        self.destino_id = destino_id
        self.saldo = saldo
        self.fid_destino = fid_destino
        self.cargo_destino = cargo_destino

        linha = discord.ui.ActionRow()
        botao_ok = discord.ui.Button(
            label="Continuar",
            style=discord.ButtonStyle.success,
            emoji="✅",
        )
        botao_ok.callback = self._ao_continuar
        botao_no = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
            emoji="❌",
        )
        botao_no.callback = self._ao_cancelar
        linha.add_item(botao_ok)
        linha.add_item(botao_no)

        self.add_item(
            discord.ui.Container(
                discord.ui.Section(
                    "# 💸 Transferir moedas",
                    corpo,
                    accessory=discord.ui.Thumbnail(url_thumb),
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha,
                discord.ui.TextDisplay("-# Transferência entre membros da hierarquia"),
                accent_color=discord.Color.blurple(),
            )
        )

    async def _ao_cancelar(self, interacao: discord.Interaction):
        if interacao.user.id != self.remetente_id:
            return
        await responder_aviso(
            interacao,
            titulo="Cancelado",
            linhas=["Transferência cancelada."],
            delay=8,
        )

    async def _ao_continuar(self, interacao: discord.Interaction):
        if interacao.user.id != self.remetente_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Este botão não é seu."],
            )
            return
        await interacao.response.send_modal(
            ModalQuantidadeTransferencia(
                remetente_id=self.remetente_id,
                destino_id=self.destino_id,
                saldo=self.saldo,
                fid_destino=self.fid_destino,
                cargo_destino=self.cargo_destino,
                mensagem_a_editar=interacao.message,
            )
        )


class ModalQuantidadeTransferencia(
    LoggingModalMixin, discord.ui.Modal, title="💸 Quantidade de moedas"
):
    quantidade_input = discord.ui.TextInput(
        label="Quantidade",
        placeholder="Ex: 5",
        required=True,
        max_length=6,
    )

    def __init__(
        self,
        *,
        remetente_id: int,
        destino_id: int,
        saldo: int,
        fid_destino: str,
        cargo_destino: str,
        mensagem_a_editar: discord.Message | None,
    ):
        super().__init__()
        self.remetente_id = remetente_id
        self.destino_id = destino_id
        self.saldo = saldo
        self.fid_destino = fid_destino
        self.cargo_destino = cargo_destino
        self.mensagem_a_editar = mensagem_a_editar
        self.quantidade_input.placeholder = f"Saldo disponível: {saldo}"

    async def on_submit(self, interacao: discord.Interaction):
        """Valida a quantidade e prepara a confirmação da transferência.

        A mensagem original é atualizada quando possível para manter o fluxo no mesmo
        card; se essa atualização visual falhar após o modal, envia uma resposta
        efêmera para não perder a confirmação nem concluir a transferência cedo demais.
        """
        bruto = self.quantidade_input.value.strip()
        if not bruto.isdigit():
            await responder_erro(
                interacao,
                titulo="Quantidade inválida",
                linhas=["Informe só números."],
            )
            return
        quantidade = int(bruto)
        if quantidade <= 0:
            await responder_erro(
                interacao,
                titulo="Quantidade inválida",
                linhas=["Informe um valor maior que zero."],
            )
            return
        if quantidade > self.saldo:
            await responder_erro(
                interacao,
                titulo="Saldo insuficiente",
                linhas=[f"Você tem **{self.saldo}** moeda(s)."],
            )
            return

        guilda = interacao.guild
        destino = guilda.get_member(self.destino_id) if guilda else None
        if destino is None and guilda is not None:
            try:
                destino = await guilda.fetch_member(self.destino_id)
            except discord.HTTPException:
                destino = None
        if destino is None:
            await responder_erro(
                interacao,
                titulo="Destinatário sumiu",
                linhas=["Não encontrei o membro no servidor."],
            )
            return

        fid_rem = await _fid(interacao.user)  # type: ignore[arg-type]
        saldo_apos = self.saldo - quantidade
        corpo = (
            f"> `🪙` * **Quantidade:** `{quantidade} moedas`\n"
            f"> `💵` * **Equivalente:** `{equivalente_em_reais(quantidade)}`\n"
            f"> `📤` * **De:** {interacao.user.mention} | FID `{fid_rem}`\n"
            f"> `📥` * **Para:** {destino.mention} | FID `{self.fid_destino}`\n"
            f"> `🏷️` * **Cargo do destino:** `{self.cargo_destino}`\n"
            f"> `💰` * **Seu saldo após:** `{saldo_apos} moedas`\n\n"
            "## Atenção\n"
            "A transferência é **imediata** e **não pode ser desfeita** pelo bot."
        )
        view = ViewConfirmarEnvioTransferencia(
            remetente_id=self.remetente_id,
            destino_id=self.destino_id,
            quantidade=quantidade,
            corpo=corpo,
            url_thumb=destino.display_avatar.url,
        )
        if self.mensagem_a_editar is not None:
            try:
                await self.mensagem_a_editar.edit(view=view)
                await interacao.response.defer()
                return
            except discord.HTTPException as erro_em_on_submit:
                # Enfeite que falhou: atualizar a mensagem depois do formulario.
                # A acao principal ja tinha dado certo, entao so registro.
                ignorar_falha_cosmetica(
                    erro_em_on_submit,
                    o_que_falhou="atualizar a mensagem depois do formulario",
                )
        await responder_view(
            interacao,
            view,
            ephemeral=True,
        )


class ViewConfirmarEnvioTransferencia(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        *,
        remetente_id: int,
        destino_id: int,
        quantidade: int,
        corpo: str,
        url_thumb: str,
    ):
        super().__init__(timeout=180)
        self.remetente_id = remetente_id
        self.destino_id = destino_id
        self.quantidade = quantidade

        linha = discord.ui.ActionRow()
        botao_ok = discord.ui.Button(
            label="Confirmar envio",
            style=discord.ButtonStyle.success,
            emoji="✅",
        )
        botao_ok.callback = self._ao_confirmar
        botao_no = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
            emoji="❌",
        )
        botao_no.callback = self._ao_cancelar
        linha.add_item(botao_ok)
        linha.add_item(botao_no)

        self.add_item(
            discord.ui.Container(
                discord.ui.Section(
                    "# 💸 Confirmar transferência",
                    corpo,
                    accessory=discord.ui.Thumbnail(url_thumb),
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha,
                discord.ui.TextDisplay("-# Confira os dados antes de confirmar"),
                accent_color=discord.Color.orange(),
            )
        )

    async def _ao_cancelar(self, interacao: discord.Interaction):
        if interacao.user.id != self.remetente_id:
            return
        await responder_aviso(
            interacao,
            titulo="Cancelado",
            linhas=["Transferência cancelada."],
            delay=8,
        )

    async def _ao_confirmar(self, interacao: discord.Interaction):
        if interacao.user.id != self.remetente_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Este botão não é seu."],
            )
            return
        if not isinstance(interacao.user, discord.Member) or interacao.guild is None:
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use dentro do servidor."],
            )
            return

        await interacao.response.defer(ephemeral=True)
        destino = interacao.guild.get_member(self.destino_id)
        if destino is None:
            try:
                destino = await interacao.guild.fetch_member(self.destino_id)
            except discord.HTTPException:
                destino = None
        if destino is None:
            await responder_erro(
                interacao,
                titulo="Destinatário sumiu",
                linhas=["Não encontrei o membro."],
            )
            return

        ok, mensagem, saldo_rem, saldo_dest = await transferir_moedas(
            remetente=interacao.user,
            destinatario=destino,
            quantidade=self.quantidade,
        )
        if not ok:
            await responder_erro(
                interacao,
                titulo="Transferência não realizada",
                linhas=[mensagem],
            )
            return

        fid_dest = await _fid(destino)
        await responder_sucesso(
            interacao,
            titulo="✅ Transferência realizada",
            linhas=[
                f"**Enviado:** `{self.quantidade} moedas` "
                f"(`{equivalente_em_reais(self.quantidade)}`)",
                f"**Para:** {destino.mention} | FID `{fid_dest}`",
                f"**Seu saldo:** `{saldo_rem} moedas`",
                "O destinatário foi notificado na DM (se estiver aberta).",
            ],
            delay=25,
        )

        fid_rem = await _fid(interacao.user)
        await enviar_dm_card(
            interacao.user,
            titulo="📤 Moedas enviadas",
            linhas=[
                f"> `🪙` * **Valor:** `-{self.quantidade} moedas`",
                f"> `📥` * **Para:** {destino.mention} | FID `{fid_dest}`",
                f"> `💰` * **Saldo:** `{saldo_rem} moedas`",
            ],
            cor=COR_AVISO,
            guilda=interacao.guild,
        )
        await enviar_dm_card(
            destino,
            titulo="📥 Moedas recebidas",
            linhas=[
                f"> `🪙` * **Valor:** `+{self.quantidade} moedas`",
                f"> `📤` * **De:** {interacao.user.mention} | FID `{fid_rem}`",
                f"> `💰` * **Saldo:** `{saldo_dest} moedas`",
            ],
            cor=COR_SUCESSO,
            guilda=interacao.guild,
        )

        try:
            from src.plantao.carteira_ranking_service import atualizar_ranking_moedas

            await atualizar_ranking_moedas(interacao.client)
        except Exception as erro_ao_atualizar_ranking:
            # A transferencia JA foi concluida e ja apareceu no log. O que
            # falhou foi so repintar o ranking de moedas, que se atualiza
            # sozinho na proxima vez. Registro para nao ficar invisivel.
            await capturar_erro_e_logar(
                erro_ao_atualizar_ranking,
                contexto="atualizar o ranking de moedas depois da transferencia",
                guilda=interacao.guild,
            )


# ---------------------------------------------------------------------------
# Depósito $ → moedas
# ---------------------------------------------------------------------------


class ViewIntroDeposito(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, *, membro_id: int, saldo: int):
        super().__init__(timeout=180)
        self.membro_id = membro_id
        self.saldo = saldo

        corpo = (
            "> Troca **inversa**: você envia o dinheiro **in-game** e, "
            "após aprovação, recebe **moedas**.\n\n"
            f"> `🪙` * **Saldo atual:** `{saldo} moedas`\n"
            f"> `💵` * **Valor por moeda:** "
            f"`{formatar_dinheiro(VALOR_MOEDA_INGAME)}`\n\n"
            "## Como funciona\n"
            "1. Informe quantas moedas deseja **comprar** com $.\n"
            "2. O pedido vai para a **equipe financeira**.\n"
            "3. Você transfere o $ in-game conforme orientação.\n"
            "4. Após confirmação, as moedas são **creditadas**."
        )
        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="Abrir pedido",
            style=discord.ButtonStyle.primary,
            emoji="📝",
        )
        botao.callback = self._ao_abrir
        linha.add_item(botao)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(f"# 📥 Depositar — $ in-game → moedas\n{corpo}"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha,
                discord.ui.TextDisplay("-# Sujeito à aprovação da equipe"),
                accent_color=discord.Color.dark_gold(),
            )
        )

    async def _ao_abrir(self, interacao: discord.Interaction):
        if interacao.user.id != self.membro_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Este botão não é seu."],
            )
            return
        await interacao.response.send_modal(
            ModalPedidoDeposito(membro_id=self.membro_id)
        )


class ModalPedidoDeposito(
    LoggingModalMixin, discord.ui.Modal, title="📥 Pedido de depósito"
):
    quantidade_input = discord.ui.TextInput(
        label="Quantidade de moedas",
        placeholder="Ex: 10",
        required=True,
        max_length=6,
    )
    observacao_input = discord.ui.TextInput(
        label="Observação / comprovante",
        style=discord.TextStyle.paragraph,
        placeholder="Ex.: Já transferi no banco da cidade",
        required=False,
        max_length=400,
    )

    def __init__(self, *, membro_id: int):
        super().__init__()
        self.membro_id = membro_id

    async def on_submit(self, interacao: discord.Interaction):
        """Cria um pedido de crédito sujeito à confirmação financeira.

        Além de gravar o pedido pendente no banco, publica um card para a equipe
        financeira analisar o valor in-game. A separação evita que moedas sejam
        creditadas automaticamente antes de a transferência ser confirmada.
        """
        if interacao.user.id != self.membro_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Este modal não é seu."],
            )
            return
        if not isinstance(interacao.user, discord.Member) or interacao.guild is None:
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use dentro do servidor."],
            )
            return

        bruto = self.quantidade_input.value.strip()
        if not bruto.isdigit() or int(bruto) <= 0:
            await responder_erro(
                interacao,
                titulo="Quantidade inválida",
                linhas=["Informe um número inteiro maior que zero."],
            )
            return
        quantidade = int(bruto)
        obs = (self.observacao_input.value or "").strip() or None
        fid = await _fid(interacao.user)

        await interacao.response.defer(ephemeral=True)
        ok, mensagem, pedido = await criar_pedido_deposito(
            membro=interacao.user,
            quantidade=quantidade,
            observacao=obs,
            id_fivem=fid if fid != "—" else None,
        )
        if not ok or pedido is None:
            await responder_erro(
                interacao,
                titulo="Pedido não criado",
                linhas=[mensagem],
            )
            return

        postou = await publicar_pedido_deposito_staff(
            interacao.guild,
            membro=interacao.user,
            pedido=pedido,
        )
        await responder_sucesso(
            interacao,
            titulo="Pedido enviado",
            linhas=[
                f"Pedido `#{pedido.id}` · **{quantidade}** moedas "
                f"(`{equivalente_em_reais(quantidade)}`).",
                "A equipe financeira vai analisar o depósito in-game.",
                (
                    "Card postado no canal de depósitos."
                    if postou
                    else "Aviso: canal de depósito não configurado — avise a staff."
                ),
            ],
            delay=25,
        )


async def publicar_pedido_deposito_staff(
    guilda: discord.Guild,
    *,
    membro: discord.Member,
    pedido,
) -> bool:
    """Publica o pedido pendente para análise e guarda o vínculo com a mensagem.

    Envia no canal financeiro um card persistente de aprovação ou recusa e grava no
    banco os identificadores do canal e da mensagem. Retorna ``False`` quando o
    canal não está configurado ou o Discord não aceita o envio, preservando o pedido
    pendente para que a equipe possa tratá-lo sem conceder moedas por engano.
    """
    canal_id = CANAIS.get("CANAL_DEPOSITO_MOEDAS") or CANAIS.get("CANAL_FINANCAS") or 0
    canal = guilda.get_channel(int(canal_id)) if canal_id else None
    if canal is None:
        return False

    corpo = (
        f"> `👤` * **Membro:** {membro.mention} | **FID:** "
        f"`{pedido.id_fivem or '—'}`\n"
        f"> `🪙` * **Moedas pedidas:** `{pedido.quantidade_moedas}`\n"
        f"> `💵` * **Valor in-game:** "
        f"`{formatar_dinheiro(pedido.valor_ingame)}`\n"
        f"> `📝` * **Obs.:** `{pedido.observacao or '—'}`\n"
        f"> `🆔` * **Pedido:** `#{pedido.id}`\n\n"
        "## Ação da equipe\n"
        "Confirme o recebimento do $ in-game antes de creditar."
    )
    view = ViewDecisaoDeposito(
        pedido_id=pedido.id, corpo=corpo, url_thumb=membro.display_avatar.url
    )
    try:
        mensagem = await canal.send(view=view)
        from src.database.conexao import async_session
        from src.database.models import PedidoDepositoMoeda

        async with async_session() as sessao:
            pedido_no_banco = await sessao.get(PedidoDepositoMoeda, pedido.id)
            if pedido_no_banco is not None:
                pedido_no_banco.mensagem_canal_id = canal.id
                pedido_no_banco.mensagem_id = mensagem.id
                await sessao.commit()
        return True
    except discord.HTTPException:
        return False


class ViewDecisaoDeposito(LoggingViewMixin, discord.ui.LayoutView):
    """Card no canal staff — botões com custom_id estável para persistir."""

    def __init__(self, *, pedido_id: int, corpo: str, url_thumb: str):
        super().__init__(timeout=None)
        self.pedido_id = pedido_id

        linha = discord.ui.ActionRow()
        botao_ok = discord.ui.Button(
            label="Creditar moedas",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"{CUSTOM_ID_DEP_APROVAR}{pedido_id}",
        )
        botao_ok.callback = self._ao_aprovar
        botao_no = discord.ui.Button(
            label="Recusar",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"{CUSTOM_ID_DEP_RECUSAR}{pedido_id}",
        )
        botao_no.callback = self._ao_recusar
        linha.add_item(botao_ok)
        linha.add_item(botao_no)

        self.add_item(
            discord.ui.Container(
                discord.ui.Section(
                    "# 📥 Pedido de depósito ( $ → moedas )",
                    corpo,
                    accessory=discord.ui.Thumbnail(url_thumb),
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha,
                discord.ui.TextDisplay("-# Depósito · aguardando análise"),
                accent_color=discord.Color.dark_gold(),
            )
        )

    async def _ao_aprovar(self, interacao: discord.Interaction):
        await _processar_decisao_deposito(interacao, self.pedido_id, aprovar=True)

    async def _ao_recusar(self, interacao: discord.Interaction):
        await _processar_decisao_deposito(interacao, self.pedido_id, aprovar=False)


async def _processar_decisao_deposito(
    interacao: discord.Interaction,
    pedido_id: int,
    *,
    aprovar: bool,
) -> None:
    if not isinstance(interacao.user, discord.Member) or interacao.guild is None:
        await responder_erro(
            interacao,
            titulo="Contexto inválido",
            linhas=["Use no servidor."],
        )
        return

    await interacao.response.defer(ephemeral=True)

    if aprovar:
        ok, mensagem, pedido = await aprovar_deposito(
            pedido_id=pedido_id,
            staff=interacao.user,
        )
    else:
        ok, mensagem, pedido = await recusar_deposito(
            pedido_id=pedido_id,
            staff=interacao.user,
        )

    if not ok or pedido is None:
        await responder_erro(
            interacao,
            titulo="Não processado",
            linhas=[mensagem],
        )
        return

    aluno = interacao.guild.get_member(pedido.discord_id)
    if aprovar:
        await responder_sucesso(
            interacao,
            titulo="Depósito creditado",
            linhas=[
                f"Pedido `#{pedido.id}` · **+{pedido.quantidade_moedas}** moedas.",
            ],
            delay=15,
        )
        if aluno is not None:
            saldo = await obter_saldo(aluno.id)
            await enviar_dm_card(
                aluno,
                titulo="✅ Depósito creditado",
                linhas=[
                    f"> `🪙` * **Creditado:** `+{pedido.quantidade_moedas} moedas`",
                    f"> `💵` * **Referente a:** "
                    f"`{formatar_dinheiro(pedido.valor_ingame)}`",
                    f"> `💰` * **Saldo:** `{saldo} moedas`",
                ],
                cor=COR_SUCESSO,
                guilda=interacao.guild,
            )
        try:
            from src.plantao.carteira_ranking_service import atualizar_ranking_moedas

            await atualizar_ranking_moedas(interacao.client)
        except Exception as erro_ao_atualizar_ranking:
            # O deposito JA foi creditado. Aqui so o ranking nao repintou.
            await capturar_erro_e_logar(
                erro_ao_atualizar_ranking,
                contexto="atualizar o ranking de moedas depois do deposito",
                guilda=interacao.guild,
            )
    else:
        await responder_aviso(
            interacao,
            titulo="Pedido recusado",
            linhas=[f"Pedido `#{pedido.id}` recusado."],
            delay=12,
        )
        if aluno is not None:
            await enviar_dm_card(
                aluno,
                titulo="❌ Depósito recusado",
                linhas=[
                    f"> `🪙` * **Pedido:** `{pedido.quantidade_moedas} moedas` "
                    "não creditadas",
                    f"> `📝` * **Motivo:** "
                    f"`{pedido.motivo_recusa or 'Recusado pela equipe'}`",
                ],
                cor=COR_ERRO,
                guilda=interacao.guild,
            )

    # Desativa botões editando a mensagem
    try:
        if interacao.message is not None:
            status = "APROVADO" if aprovar else "RECUSADO"
            await interacao.message.edit(
                view=ViewDecisaoDepositoFinal(
                    pedido_id=pedido_id,
                    status=status,
                    staff=interacao.user,
                )
            )
    except discord.HTTPException as erro_em_processar_decisao_deposito:
        # Enfeite que falhou: atualizar o card do deposito.
        # A acao principal ja tinha dado certo, entao so registro.
        ignorar_falha_cosmetica(
            erro_em_processar_decisao_deposito,
            o_que_falhou="atualizar o card do deposito",
        )


class ViewDecisaoDepositoFinal(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, *, pedido_id: int, status: str, staff: discord.Member):
        super().__init__(timeout=None)
        emoji = "✅" if status == "APROVADO" else "❌"
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"# 📥 Pedido `#{pedido_id}` — {emoji} {status}\n"
                    f"Analisado por {staff.mention}"
                ),
                accent_color=(
                    discord.Color.green()
                    if status == "APROVADO"
                    else discord.Color.red()
                ),
            )
        )


def view_persistente_deposito_stub() -> ViewDecisaoDeposito:
    """View só para registrar custom_ids no restart (pedido_id=0 não é usado)."""
    return ViewDecisaoDeposito(
        pedido_id=0,
        corpo="_stub_",
        url_thumb="https://cdn.discordapp.com/embed/avatars/0.png",
    )
