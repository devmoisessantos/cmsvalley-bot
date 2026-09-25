"""Painéis e botões do fluxo de cursos (solicitar → agendar → aceitar → decidir)."""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord

from src.config import CANAIS
from src.cursos.cursos_service import (
    aceitar_agendamento,
    buscar_pedido_aberto,
    calcular_cobranca_pacote,
    conceder_cargos_dos_cursos,
    creditar_moedas_instrutor,
    debitar_moedas_curso,
    decidir_cursos_parciais,
    limpar_mensagem_solicitacao_curso,
    listar_cursos_ordenados,
    marcar_grupo_repasse,
    marcar_mensagem_solicitacao_curso,
    membro_tem_curso,
    menção_cargo_curso,
    mesclar_cursos_no_pedido,
    montar_linhas_corpo_pedido,
    obter_curso,
    obter_solicitacao_curso,
    parse_chaves_json,
    proximo_grupo_repasse_pendente,
    recusar_agendamento,
    registrar_solicitacao_pacote,
    rotulo_curso,
    soma_valor_ingame,
)
from src.plantao.plantao_permissoes import e_diretoria
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
    enviar_erro_para_log_erros,
    ignorar_falha_cosmetica,
)
from src.utils.formatacao import formatar_reais
from src.utils.mensagens import (
    COR_SUCESSO,
    editar_mensagem_original,
    responder_aviso,
    responder_erro,
    responder_sucesso,
    responder_view,
)
from src.utils.notificacao import enviar_dm_card

CUSTOM_ID_BOTAO_SELECIONAR = "cursos:botao_selecionar"
CUSTOM_ID_SELECT_MULTI = "cursos:select_multi"
CUSTOM_ID_ACEITAR = "cursos:aceitar:"
CUSTOM_ID_RECUSAR = "cursos:recusar:"
CUSTOM_ID_APROVAR = "cursos:aprovar:"
CUSTOM_ID_REPROVAR = "cursos:reprovar:"
CUSTOM_ID_CONFIRMA_APROVAR = "cursos:confirma_aprovar:"
CUSTOM_ID_CONFIRMA_REPROVAR = "cursos:confirma_reprovar:"
CUSTOM_ID_CANCELA_DECISAO = "cursos:cancela_decisao:"
CUSTOM_ID_REGISTRAR_REPASSE = "cursos:registrar_repasse:"

PRAZO_COMPROVANTE_REPASSE_SEGUNDOS = 300


def _instrutor_ou_diretoria(membro: discord.Member) -> bool:
    if e_diretoria(membro):
        return True
    # Instrutores: qualquer cargo cujo nome contenha Instrutor
    for cargo in membro.roles:
        nome = (cargo.name or "").lower()
        if "instrutor" in nome:
            return True
    return False


def _mencao_instrutor(
    guilda: discord.Guild | None,
    instrutor_id: int | None,
) -> str:
    """Menção do instrutor que aceitou o pedido, ou texto neutro."""
    if not instrutor_id:
        return "_não definido_"
    if guilda is not None:
        membro = guilda.get_member(int(instrutor_id))
        if membro is not None:
            return membro.mention
    return f"<@{int(instrutor_id)}>"


def _bloco_observacao_instrutor(
    guilda: discord.Guild | None,
    registro,
) -> str:
    """
    Cabeçalho fixo: ### 📌 Observação do instrutor: @instrutor
    """
    mencao = _mencao_instrutor(guilda, getattr(registro, "instrutor_id", None))
    texto = (getattr(registro, "observacao_instrutor", None) or "").strip()
    if texto:
        return f"\n\n### 📌 Observação do instrutor: {mencao}\n> {texto}"
    return f"\n\n### 📌 Observação do instrutor: {mencao}"


def _pode_decidir_pedido(
    membro: discord.Member,
    registro,
) -> bool:
    """
    Quem pode aprovar, reprovar ou registrar repasse:

    - o instrutor que aceitou o pedido
    - a equipe de Diretoria (intervém se o instrutor sair ou não concluir)
    """
    if e_diretoria(membro):
        return True
    instrutor_id = getattr(registro, "instrutor_id", None)
    if not instrutor_id:
        return False
    return int(instrutor_id) == int(membro.id)


# ---------------------------------------------------------------------------
# Painel persistente
# ---------------------------------------------------------------------------


class PainelCursosLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel fixo no padrão do recrutamento (Section + checklist + botão)."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(timeout=None)

        linha_botoes = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="Selecionar cursos",
            style=discord.ButtonStyle.success,
            emoji="📚",
            custom_id=CUSTOM_ID_BOTAO_SELECIONAR,
        )
        botao.callback = self._ao_abrir_selecao
        linha_botoes.add_item(botao)

        url_icone = None
        if guild is not None and guild.icon is not None:
            url_icone = guild.icon.url

        texto_titulo = (
            "Solicite um ou mais cursos do hospital.\n\n"
            "O pedido segue para **agendamentos**; um instrutor aceita, "
            "aplica o curso e a diretoria/instrutor finaliza a aprovação."
        )
        # Section + Thumbnail só com ícone — accessory=None quebra o LayoutView
        if url_icone:
            bloco_topo = discord.ui.Section(
                "# 📚 Painel de Cursos",
                texto_titulo,
                accessory=discord.ui.Thumbnail(url_icone),
            )
        else:
            bloco_topo = discord.ui.TextDisplay(
                "# 📚 Painel de Cursos\n" + texto_titulo
            )

        self.add_item(
            discord.ui.Container(
                bloco_topo,
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(
                    "## 📌 Antes de solicitar\n\n"
                    "✅ Veja os **valores** dos cursos antes de pagar.\n"
                    "✅ Você pode marcar **vários** cursos de uma vez.\n"
                    "✅ Informe data/horário se quiser (ou deixe em branco).\n"
                    "✅ Pagamento **obrigatório in-game**; moedas só como "
                    "**desconto** (até 10 por pedido).\n"
                    "✅ Resgate: Enfermeiro com **6h+** de plantão no ciclo = "
                    "**grátis**.\n"
                    "✅ Curso concluído = você recebe o **cargo** correspondente.\n"
                    "✅ Se já tiver um pedido aberto, novos cursos **entram no mesmo "
                    "card**."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha_botoes,
                accent_color=discord.Color.dark_teal(),
            )
        )

    async def _ao_abrir_selecao(self, interacao: discord.Interaction):
        try:
            await responder_view(
                interacao,
                SeletorMultiCursosView(interacao.user.id),
                ephemeral=True,
            )
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro ao abrir seletor de cursos",
                erro,
                contexto="PainelCursosLayout._ao_abrir_selecao",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=["Não foi possível abrir a seleção de cursos."],
            )


class SeletorMultiCursosView(LoggingViewMixin, discord.ui.LayoutView):
    """Select efêmero: um ou vários cursos (texto mínimo)."""

    def __init__(self, solicitante_id: int):
        super().__init__(timeout=180)
        self.solicitante_id = solicitante_id

        opcoes: list[discord.SelectOption] = []
        for chave, dados in listar_cursos_ordenados():
            valor = int(dados.get("valor_ingame") or 0)
            desc = (
                f"{dados.get('nivel', '—')} · {formatar_reais(valor)}"
                if valor > 0
                else f"{dados.get('nivel', '—')} · a combinar"
            )
            opcoes.append(
                discord.SelectOption(
                    label=dados["nome"][:100],
                    value=chave,
                    description=desc[:100],
                    emoji=dados.get("emoji") or None,
                )
            )
        opcoes = opcoes[:25]

        linha = discord.ui.ActionRow()
        seletor = discord.ui.Select(
            placeholder="Marque um ou mais cursos…",
            options=opcoes,
            min_values=1,
            max_values=min(10, len(opcoes)),
            custom_id=CUSTOM_ID_SELECT_MULTI,
        )
        seletor.callback = self._ao_confirmar_selecao
        linha.add_item(seletor)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# Escolha os cursos\n"
                    "Marque e confirme. Em seguida informe data/horário (opcional)."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha,
                accent_color=discord.Color.dark_teal(),
            )
        )

    async def _ao_confirmar_selecao(self, interacao: discord.Interaction):
        if interacao.user.id != self.solicitante_id:
            await responder_erro(
                interacao,
                titulo="Não é sua seleção",
                linhas=["Só quem abriu o painel pode continuar."],
            )
            return
        valores = interacao.data.get("values") if interacao.data else None
        if not valores:
            await responder_erro(
                interacao,
                titulo="Nada selecionado",
                linhas=["Escolha pelo menos um curso."],
            )
            return

        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Apenas no servidor",
                linhas=["Use o painel dentro do Discord do hospital."],
            )
            return

        chaves_validas: list[str] = []
        ja_tem: list[str] = []
        for chave in valores:
            if membro_tem_curso(membro, chave):
                ja_tem.append(rotulo_curso(chave))
            else:
                chaves_validas.append(chave)

        if not chaves_validas:
            await responder_aviso(
                interacao,
                titulo="Você já concluiu esses cursos",
                linhas=[
                    "Nenhum curso novo na seleção.",
                    *([f"Já possui: {', '.join(ja_tem)}"] if ja_tem else []),
                ],
                delay=15,
            )
            return

        # Modal consome a response; a mensagem do select fica órfã —
        # guardamos o id para apagar depois no on_submit do modal.
        id_mensagem_seletor = (
            interacao.message.id if interacao.message is not None else None
        )
        await interacao.response.send_modal(
            ModalObservacaoAluno(
                chaves=chaves_validas,
                solicitante_id=self.solicitante_id,
                avisos_ja_tem=ja_tem,
                id_mensagem_seletor=id_mensagem_seletor,
            )
        )


class ModalObservacaoAluno(LoggingModalMixin, discord.ui.Modal):
    """Data/horário livre — vazio = sem observação."""

    def __init__(
        self,
        *,
        chaves: list[str],
        solicitante_id: int,
        avisos_ja_tem: list[str] | None = None,
        id_mensagem_seletor: int | None = None,
    ):
        super().__init__(title="Observação do pedido")
        self.chaves = chaves
        self.solicitante_id = solicitante_id
        self.avisos_ja_tem = avisos_ja_tem or []
        self.id_mensagem_seletor = id_mensagem_seletor
        self.campo_observacao = discord.ui.TextInput(
            label="Data / horário ou observação",
            style=discord.TextStyle.paragraph,
            placeholder="Ex.: Sábado 14h na call de cursos — ou deixe em branco",
            required=False,
            max_length=500,
        )
        self.add_item(self.campo_observacao)

    async def on_submit(self, interacao: discord.Interaction):
        """Leva a seleção do aluno para a confirmação de pagamento privada.

        Confere o autor da solicitação para impedir que alguém aproveite um
        modal alheio. Depois envia a visualização com os cursos e tenta apagar
        a mensagem transitória do seletor, sem desfazer o pedido se isso falhar.
        """
        if interacao.user.id != self.solicitante_id:
            await responder_erro(
                interacao,
                titulo="Não é seu pedido",
                linhas=["Só quem iniciou a solicitação pode confirmar."],
            )
            return
        observacao = (self.campo_observacao.value or "").strip()
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Apenas no servidor",
                linhas=["Use o painel no Discord do hospital."],
            )
            return
        cobranca = await calcular_cobranca_pacote(
            membro,
            self.chaves,
            moedas_desconto_desejadas=0,
        )
        view = ConfirmacaoPagamentoPacoteView(
            chaves=self.chaves,
            solicitante_id=self.solicitante_id,
            observacao_aluno=observacao,
            cobranca=cobranca,
        )
        # Uma única mensagem efêmera de confirmação (substitui o fluxo anterior)
        await responder_view(
            interacao,
            view,
            ephemeral=True,
        )

        # Tenta apagar a mensagem do select (reduz spam)
        if self.id_mensagem_seletor is not None:
            try:
                await interacao.followup.delete_message(self.id_mensagem_seletor)
            except (discord.HTTPException, discord.NotFound) as erro_em_on_submit:
                # Enfeite que falhou: atualizar a mensagem depois do formulario.
                # A acao principal ja tinha dado certo, entao so registro.
                ignorar_falha_cosmetica(
                    erro_em_on_submit,
                    o_que_falhou="atualizar a mensagem depois do formulario",
                )


class ConfirmacaoPagamentoPacoteView(LoggingViewMixin, discord.ui.LayoutView):
    """
    Confirma o pedido: pagamento sempre IN_GAME.

    Moedas entram só como desconto (até o teto do pacote). Resgate isento
    quando a regra de 6h for atendida.
    """

    def __init__(
        self,
        *,
        chaves: list[str],
        solicitante_id: int,
        observacao_aluno: str,
        cobranca: dict,
    ):
        super().__init__(timeout=180)
        self.chaves = chaves
        self.solicitante_id = solicitante_id
        self.observacao_aluno = observacao_aluno
        self.cobranca = cobranca

        lista = "\n".join(
            f"• {rotulo_curso(chave)} — "
            f"{formatar_reais(int((obter_curso(chave) or {}).get('valor_ingame') or 0))}"
            for chave in chaves
        )
        obs_txt = observacao_aluno if observacao_aluno else "_Sem observação_"
        cotacao = int(cobranca.get("cotacao") or 0)
        teto = int(cobranca.get("teto_moedas") or 0)
        valor_bruto = int(cobranca.get("valor_bruto") or 0)
        isento = bool(cobranca.get("isento"))

        linha = discord.ui.ActionRow()
        if isento or valor_bruto <= 0:
            botao_gratis = discord.ui.Button(
                label="Registrar solicitação (grátis)",
                style=discord.ButtonStyle.success,
            )
            botao_gratis.callback = self._ao_gratuito
            linha.add_item(botao_gratis)
        else:
            botao_ingame = discord.ui.Button(
                label="Pagar in-game (sem desconto)",
                style=discord.ButtonStyle.primary,
            )
            botao_ingame.callback = self._ao_pagar_ingame
            linha.add_item(botao_ingame)

            if teto > 0:
                botao_desconto = discord.ui.Button(
                    label=f"Pagar in-game com desconto (até {teto} moedas)",
                    style=discord.ButtonStyle.success,
                )
                botao_desconto.callback = self._ao_pagar_com_desconto
                linha.add_item(botao_desconto)

        botao_cancelar = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
        )
        botao_cancelar.callback = self._ao_cancelar
        linha.add_item(botao_cancelar)

        texto_extra = ""
        if isento:
            texto_extra = (
                f"**Isento:** {cobranca.get('motivo_isencao') or 'gratuito'}\n"
            )
        elif valor_bruto > 0:
            texto_extra = (
                f"**Total in-game:** {formatar_reais(valor_bruto)}\n"
                f"**Sua cotação:** 1 moeda = {formatar_reais(cotacao)}\n"
                f"**Desconto máximo:** até `{teto}` moeda(s) neste pedido\n"
                "_Cursos não são pagos só com moedas — o restante é in-game._\n"
            )

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# Confirmar pedido de curso\n"
                    f"{lista}\n\n"
                    f"{texto_extra}"
                    f"**Observação:** {obs_txt}"
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha,
                accent_color=discord.Color.gold(),
            )
        )

    async def _garantir_dono(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.solicitante_id:
            await responder_erro(
                interacao,
                titulo="Não é seu pedido",
                linhas=["Só quem montou o pedido pode pagar."],
            )
            return False
        return True

    async def _ao_pagar_ingame(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await finalizar_pedido(
            interacao,
            chaves=self.chaves,
            observacao_aluno=self.observacao_aluno,
            moedas_desconto=0,
        )

    async def _ao_pagar_com_desconto(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        teto = int(self.cobranca.get("teto_moedas") or 0)
        modal = ModalDescontoMoedasCurso(
            chaves=self.chaves,
            solicitante_id=self.solicitante_id,
            observacao_aluno=self.observacao_aluno,
            teto_moedas=teto,
            cotacao=int(self.cobranca.get("cotacao") or 0),
            valor_bruto=int(self.cobranca.get("valor_bruto") or 0),
        )
        await interacao.response.send_modal(modal)

    async def _ao_gratuito(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await finalizar_pedido(
            interacao,
            chaves=self.chaves,
            observacao_aluno=self.observacao_aluno,
            moedas_desconto=0,
            forcar_gratuito=True,
        )

    async def _ao_cancelar(self, interacao: discord.Interaction):
        await responder_aviso(
            interacao,
            titulo="Cancelado",
            linhas=["Pedido de curso cancelado."],
            delay=8,
        )


class ModalDescontoMoedasCurso(LoggingModalMixin, discord.ui.Modal):
    """Pergunta quantas moedas (0 até o teto) usar como desconto no pedido."""

    def __init__(
        self,
        *,
        chaves: list[str],
        solicitante_id: int,
        observacao_aluno: str,
        teto_moedas: int,
        cotacao: int,
        valor_bruto: int,
    ):
        super().__init__(title="Desconto em moedas")
        self.chaves = chaves
        self.solicitante_id = solicitante_id
        self.observacao_aluno = observacao_aluno
        self.teto_moedas = max(0, int(teto_moedas))
        self.cotacao = int(cotacao)
        self.valor_bruto = int(valor_bruto)
        self.campo_quantidade = discord.ui.TextInput(
            label=f"Moedas (0 a {self.teto_moedas})",
            placeholder=f"Máximo {self.teto_moedas}",
            required=True,
            max_length=3,
        )
        self.add_item(self.campo_quantidade)

    async def on_submit(self, interacao: discord.Interaction):
        if interacao.user.id != self.solicitante_id:
            await responder_erro(
                interacao,
                titulo="Não é seu pedido",
                linhas=["Só quem montou o pedido pode pagar."],
            )
            return
        bruto = (self.campo_quantidade.value or "").strip()
        try:
            quantidade = int(bruto)
        except ValueError:
            await responder_erro(
                interacao,
                titulo="Quantidade inválida",
                linhas=["Informe um número inteiro de moedas."],
            )
            return
        if quantidade < 0 or quantidade > self.teto_moedas:
            await responder_erro(
                interacao,
                titulo="Fora do limite",
                linhas=[
                    f"Use de 0 a {self.teto_moedas} moedas neste pedido.",
                ],
            )
            return
        await finalizar_pedido(
            interacao,
            chaves=self.chaves,
            observacao_aluno=self.observacao_aluno,
            moedas_desconto=quantidade,
        )


async def finalizar_pedido(
    interacao: discord.Interaction,
    *,
    chaves: list[str],
    observacao_aluno: str,
    moedas_desconto: int = 0,
    forcar_gratuito: bool = False,
) -> None:
    """Cria ou amplia o pedido de cursos e o encaminha para agendamento.

    Pagamento base é sempre IN_GAME (ou GRATUITO na isenção de Resgate).
    Moedas só entram como desconto, debitadas aqui se o aluno pediu.
    """
    membro = interacao.user
    if not isinstance(membro, discord.Member):
        await responder_erro(
            interacao,
            titulo="Apenas no servidor",
            linhas=["Use o painel no Discord do hospital."],
        )
        return

    try:
        if not interacao.response.is_done():
            await interacao.response.defer(ephemeral=True)

        # Já possui o cargo = não pode pedir de novo
        chaves_novas: list[str] = []
        ja_tem: list[str] = []
        for chave in chaves:
            if membro_tem_curso(membro, chave):
                ja_tem.append(rotulo_curso(chave))
            else:
                chaves_novas.append(chave)

        if not chaves_novas:
            await responder_aviso(
                interacao,
                titulo="Nada novo para solicitar",
                linhas=[
                    "Você já possui o cargo de todos os cursos escolhidos.",
                    *([f"Já concluídos: {', '.join(ja_tem)}"] if ja_tem else []),
                ],
                delay=15,
            )
            return

        pedido_aberto = await buscar_pedido_aberto(membro.id)
        chaves_no_pedido: list[str] = []
        if pedido_aberto is not None:
            chaves_no_pedido = parse_chaves_json(
                pedido_aberto.chaves_cursos_json,
                pedido_aberto.chave_curso,
            )

        # Só cobra / acrescenta o que ainda não está no pedido aberto
        chaves_para_adicionar = [
            chave for chave in chaves_novas if chave not in chaves_no_pedido
        ]
        if not chaves_para_adicionar:
            await responder_aviso(
                interacao,
                titulo="Cursos já estão no seu pedido aberto",
                linhas=[
                    f"Pedido `#{pedido_aberto.id}` já inclui: "
                    + ", ".join(
                        rotulo_curso(chave_do_curso) for chave_do_curso in chaves_novas
                    ),
                    "Não é criado um segundo card.",
                ],
                delay=18,
            )
            return

        cobranca = await calcular_cobranca_pacote(
            membro,
            chaves_para_adicionar,
            moedas_desconto_desejadas=moedas_desconto,
        )
        if forcar_gratuito and not cobranca.get("isento"):
            # Botão grátis só deve aparecer quando já calculamos isenção
            await responder_erro(
                interacao,
                titulo="Isenção não aplicável",
                linhas=[
                    "Este pacote não está isento. Use pagamento in-game "
                    "(com ou sem desconto em moedas).",
                ],
            )
            return

        moedas = int(cobranca.get("moedas_desconto") or 0)
        if cobranca.get("isento"):
            forma = "GRATUITO"
        elif moedas > 0:
            forma = "IN_GAME_COM_DESCONTO"
        else:
            forma = "IN_GAME"
        valor_a_pagar = int(cobranca.get("valor_a_pagar_ingame") or 0)
        cotacao = int(cobranca.get("cotacao") or 0)
        saldo_restante = None
        if moedas > 0:
            ok, saldo_restante, erro_txt = await debitar_moedas_curso(
                membro.id,
                moedas,
            )
            if not ok:
                await responder_erro(
                    interacao,
                    titulo="Saldo insuficiente",
                    linhas=[erro_txt],
                )
                return

        if pedido_aberto is not None:
            registro = await mesclar_cursos_no_pedido(
                solicitacao_id=pedido_aberto.id,
                novas_chaves=chaves_para_adicionar,
                forma_pagamento=forma,
                moedas_extra=moedas,
                observacao_aluno=observacao_aluno,
                cotacao_moeda=cotacao,
            )
            if registro is None:
                await responder_erro(
                    interacao,
                    titulo="Falha ao atualizar pedido",
                    linhas=["Não foi possível mesclar os cursos no pedido aberto."],
                )
                return
            ok_post = await atualizar_ou_publicar_agendamento(
                interacao.guild,
                membro=membro,
                registro=registro,
            )
            titulo_ok = "Pedido atualizado"
            linhas_ok = [
                f"Pedido `#{registro.id}` **atualizado** (sem segundo card).",
                "Novos cursos: "
                + ", ".join(
                    rotulo_curso(chave_do_curso)
                    for chave_do_curso in chaves_para_adicionar
                ),
                f"Forma: `{forma}` · A pagar: "
                f"`{formatar_reais(int(registro.valor_ingame or 0))}`"
                + (
                    f" · Desconto: `{moedas}` moeda(s) · Saldo: `{saldo_restante}`"
                    if moedas
                    else ""
                ),
            ]
        else:
            registro = await registrar_solicitacao_pacote(
                discord_id=membro.id,
                chaves=chaves_para_adicionar,
                forma_pagamento=forma,
                moedas_debitadas=moedas,
                observacao_aluno=observacao_aluno,
                valor_a_pagar_ingame=valor_a_pagar,
                cotacao_moeda=cotacao,
            )
            ok_post = await publicar_no_agendamentos(
                interacao.guild,
                membro=membro,
                registro=registro,
            )
            titulo_ok = "Pedido enviado ao agendamento"
            linhas_ok = [
                f"Pedido `#{registro.id}` publicado.",
                f"Forma: `{forma}` · A pagar: `{formatar_reais(valor_a_pagar)}`"
                + (
                    f" · Desconto: `{moedas}` moeda(s) "
                    f"({formatar_reais(cotacao)} cada) · Saldo: `{saldo_restante}`"
                    if moedas
                    else ""
                ),
                "Aguarde um instrutor **aceitar** a solicitação.",
            ]

        if not ok_post:
            await responder_erro(
                interacao,
                titulo="Pedido salvo, falha no canal",
                linhas=[
                    f"Pedido `#{registro.id}` gravado, mas o card de agendamento "
                    f"falhou.",
                    "A equipe foi notificada no log de erros.",
                ],
            )
            return

        # Substitui o card de confirmação pela resposta final (menos spam)
        if interacao.message is not None:
            try:
                texto_final = "\n".join(f"• {linha}" for linha in linhas_ok)
                view_final = discord.ui.LayoutView(timeout=60)
                view_final.add_item(
                    discord.ui.Container(
                        discord.ui.TextDisplay(f"# ✅ {titulo_ok}\n{texto_final}"),
                        accent_color=discord.Color.green(),
                    )
                )
                await interacao.message.edit(view=view_final)
            except discord.HTTPException:
                await responder_sucesso(
                    interacao,
                    titulo=titulo_ok,
                    linhas=linhas_ok,
                    delay=25,
                )
        else:
            await responder_sucesso(
                interacao,
                titulo=titulo_ok,
                linhas=linhas_ok,
                delay=25,
            )
    except Exception as erro:
        await enviar_erro_para_log_erros(
            interacao.guild,
            "Erro ao finalizar pedido de curso",
            erro,
            contexto="finalizar_pedido",
            usuario=membro,
        )
        await responder_erro(
            interacao,
            titulo="Erro inesperado",
            linhas=["Falha ao registrar o pedido. A equipe foi notificada."],
        )


# ---------------------------------------------------------------------------
# Cards de canal
# ---------------------------------------------------------------------------


def _rodape(guilda: discord.Guild | None) -> str:
    momento = int(datetime.now(timezone.utc).timestamp())
    nome = guilda.name if guilda else "CENTRO MÉDICO SUL VALLEY"
    return f"-# {nome} • <t:{momento}:f>"


class ViewAceitarAgendamento(LoggingViewMixin, discord.ui.LayoutView):
    """Mensagem em CANAL_AGENDAMENTOS — Aceitar ou Recusar."""

    def __init__(
        self,
        *,
        titulo: str,
        corpo: str,
        guild: discord.Guild,
        solicitacao_id: int,
        url_avatar: str | None,
        ja_aceito: bool = False,
        ja_recusado: bool = False,
    ):
        super().__init__(timeout=None)
        self.solicitacao_id = solicitacao_id

        componentes: list = [
            discord.ui.TextDisplay(f"# {titulo}"),
        ]
        if url_avatar:
            componentes.append(
                discord.ui.Section(
                    corpo,
                    accessory=discord.ui.Thumbnail(url_avatar),
                )
            )
        else:
            componentes.append(discord.ui.TextDisplay(corpo))

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        finalizado = ja_aceito or ja_recusado
        linha = discord.ui.ActionRow()
        if ja_recusado:
            botao_aceitar = discord.ui.Button(
                label="Recusado",
                style=discord.ButtonStyle.secondary,
                disabled=True,
                custom_id=f"{CUSTOM_ID_ACEITAR}{solicitacao_id}",
            )
            linha.add_item(botao_aceitar)
        else:
            botao_aceitar = discord.ui.Button(
                label="Aceitar Solicitação" if not ja_aceito else "Aceito ✓",
                style=(
                    discord.ButtonStyle.success
                    if not ja_aceito
                    else discord.ButtonStyle.secondary
                ),
                emoji="✅",
                custom_id=f"{CUSTOM_ID_ACEITAR}{solicitacao_id}",
                disabled=finalizado,
            )
            botao_recusar = discord.ui.Button(
                label="Recusar",
                style=discord.ButtonStyle.danger,
                custom_id=f"{CUSTOM_ID_RECUSAR}{solicitacao_id}",
                disabled=finalizado,
            )
            # Callbacks via on_interaction no cog (sobrevivem a restart)
            linha.add_item(botao_aceitar)
            linha.add_item(botao_recusar)
        componentes.append(linha)
        componentes.append(discord.ui.TextDisplay(_rodape(guild)))

        if ja_recusado:
            cor = discord.Color.red()
        elif ja_aceito:
            cor = discord.Color.green()
        else:
            cor = discord.Color.dark_gold()
        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=cor,
            )
        )

    async def _ao_aceitar(self, interacao: discord.Interaction):
        membro = interacao.user
        if not isinstance(membro, discord.Member) or not _instrutor_ou_diretoria(
            membro
        ):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas **Instrutor** ou **Diretoria** pode aceitar."],
            )
            return
        await interacao.response.send_modal(
            ModalObservacaoInstrutor(
                solicitacao_id=self.solicitacao_id,
                instrutor_id=membro.id,
                mensagem_agendamento=interacao.message,
            )
        )


class ModalObservacaoInstrutor(LoggingModalMixin, discord.ui.Modal):
    def __init__(
        self,
        *,
        solicitacao_id: int,
        instrutor_id: int,
        mensagem_agendamento: discord.Message | None,
    ):
        super().__init__(title="Aceitar agendamento")
        self.solicitacao_id = solicitacao_id
        self.instrutor_id = instrutor_id
        self.mensagem_agendamento = mensagem_agendamento
        self.campo = discord.ui.TextInput(
            label="Observação do instrutor (opcional)",
            style=discord.TextStyle.paragraph,
            placeholder="Ex.: Confirmado sábado 15h na call de cursos",
            required=False,
            max_length=500,
        )
        self.add_item(self.campo)

    async def on_submit(self, interacao: discord.Interaction):
        """Aceita o agendamento e entrega o pedido ao fluxo de decisão final.

        Reserva o pedido no banco para este instrutor, desativa o botão no card
        original e publica o próximo card no canal de aprovar ou reprovar.
        Também tenta avisar o aluno por DM e registra qualquer falha inesperada.
        """
        try:
            await interacao.response.defer(ephemeral=True)
            obs = (self.campo.value or "").strip()
            registro = await aceitar_agendamento(
                solicitacao_id=self.solicitacao_id,
                instrutor_id=self.instrutor_id,
                observacao_instrutor=obs,
            )
            if registro is None:
                await responder_erro(
                    interacao,
                    titulo="Pedido não encontrado",
                    linhas=[f"ID `#{self.solicitacao_id}`."],
                )
                return
            if registro.status != "ACEITO":
                await responder_aviso(
                    interacao,
                    titulo="Já processado",
                    linhas=[f"Status atual: `{registro.status}`."],
                    delay=10,
                )
                return

            guilda = interacao.guild
            aluno = guilda.get_member(registro.discord_id) if guilda else None

            # Remove o card do canal de agendamentos (id no banco + fallback)
            # e publica o card de decisão com id novo gravado no banco.
            await apagar_card_do_pedido(
                guilda,
                registro.id,
                mensagem_fallback=self.mensagem_agendamento,
            )

            # Publica em aprovar/reprovar (grava mensagem_id no banco)
            await publicar_para_decisao(guilda, registro=registro, aluno=aluno)

            # DM do aluno + log LOG_NOTIFICACOES_DM
            if aluno is not None:
                await enviar_dm_card(
                    aluno,
                    titulo="Agendamento de curso aceito",
                    linhas=[
                        f"Seu pedido `#{registro.id}` foi **aceito**.",
                        f"Instrutor: {interacao.user.mention}",
                        f"Observação: {obs or '_Sem observação_'}",
                        "Aguarde a **aprovação final** após a aplicação do curso.",
                    ],
                    cor=COR_SUCESSO,
                    guilda=guilda,
                )

            await responder_sucesso(
                interacao,
                titulo="Solicitação aceita",
                linhas=[
                    f"Pedido `#{registro.id}` aceito.",
                    "O aluno foi notificado na DM (se aberta).",
                    "Card enviado ao canal de **aprovar/reprovar**.",
                ],
                delay=15,
            )
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro ao aceitar agendamento de curso",
                erro,
                contexto="ModalObservacaoInstrutor.on_submit",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=["Falha ao aceitar. Veja LOG_ERROS."],
            )


class ViewDecisaoCurso(LoggingViewMixin, discord.ui.LayoutView):
    """Canal aprovar/reprovar — select por curso + confirmação."""

    def __init__(
        self,
        *,
        titulo: str,
        corpo: str,
        guild: discord.Guild,
        solicitacao_id: int,
        url_avatar: str | None,
        modo: str = "normal",
        desabilitada: bool = False,
        chaves_cursos: list[str] | None = None,
        bloquear_decisao: bool = False,
        mostrar_botao_repasse: bool = True,
    ):
        """
        modo: normal | selecionar_aprovar | selecionar_reprovar | final

        ``bloquear_decisao`` desativa Aprovar/Reprovar até o repasse
        com comprovante ser registrado.
        ``mostrar_botao_repasse`` some quando todos os comprovantes
        já foram enviados.
        """
        super().__init__(timeout=None)
        self.solicitacao_id = solicitacao_id
        self.titulo = titulo
        self.corpo = corpo
        self.guild_ref = guild
        self.url_avatar = url_avatar
        self.chaves_cursos = list(chaves_cursos or [])
        self.bloquear_decisao = bloquear_decisao
        self.mostrar_botao_repasse = mostrar_botao_repasse

        componentes: list = [discord.ui.TextDisplay(f"# {titulo}")]
        if url_avatar:
            componentes.append(
                discord.ui.Section(corpo, accessory=discord.ui.Thumbnail(url_avatar))
            )
        else:
            componentes.append(discord.ui.TextDisplay(corpo))
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        linha = discord.ui.ActionRow()
        if desabilitada or modo == "final":
            botao = discord.ui.Button(
                label="Decisão registrada",
                style=discord.ButtonStyle.secondary,
                disabled=True,
            )
            linha.add_item(botao)
            componentes.append(linha)
        elif modo in ("selecionar_aprovar", "selecionar_reprovar"):
            opcoes = []
            for chave in self.chaves_cursos[:25]:
                dados = obter_curso(chave) or {}
                opcoes.append(
                    discord.SelectOption(
                        label=(dados.get("nome") or chave)[:100],
                        value=chave,
                        emoji=dados.get("emoji") or None,
                        description="Marque para incluir nesta decisão"[:100],
                    )
                )
            if not opcoes:
                opcoes = [
                    discord.SelectOption(
                        label="Sem cursos", value="_vazio", default=True
                    )
                ]
            texto_ajuda = (
                "Marque os cursos que serão **APROVADOS**. "
                "Os não marcados serão **reprovados**."
                if modo == "selecionar_aprovar"
                else "Marque os cursos que serão **REPROVADOS**. "
                "Os não marcados serão **aprovados**."
            )
            componentes.append(discord.ui.TextDisplay(f"-# {texto_ajuda}"))
            seletor = discord.ui.Select(
                placeholder="Selecione os cursos…",
                options=opcoes,
                min_values=1,
                max_values=len(opcoes),
                custom_id=f"cursos:sel_decisao:{solicitacao_id}:{modo}",
            )
            # Callback via on_interaction no cog (sobrevive a restart)
            linha.add_item(seletor)
            componentes.append(linha)
            linha2 = discord.ui.ActionRow()
            botao_cancelar = discord.ui.Button(
                label="Cancelar",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{CUSTOM_ID_CANCELA_DECISAO}{solicitacao_id}",
            )
            # Callback via on_interaction no cog (sobrevive a restart)
            linha2.add_item(botao_cancelar)
            componentes.append(linha2)
        else:
            if mostrar_botao_repasse:
                botao_repasse = discord.ui.Button(
                    label="Registrar Pagamento",
                    style=discord.ButtonStyle.primary,
                    custom_id=f"{CUSTOM_ID_REGISTRAR_REPASSE}{solicitacao_id}",
                    disabled=False,
                )
                linha.add_item(botao_repasse)
            botao_ok = discord.ui.Button(
                label="Aprovar",
                style=discord.ButtonStyle.success,
                emoji="✅",
                custom_id=f"{CUSTOM_ID_APROVAR}{solicitacao_id}",
                disabled=bloquear_decisao,
            )
            botao_nao = discord.ui.Button(
                label="Reprovar",
                style=discord.ButtonStyle.danger,
                emoji="❌",
                custom_id=f"{CUSTOM_ID_REPROVAR}{solicitacao_id}",
                disabled=bloquear_decisao,
            )
            # Callbacks via on_interaction no cog (sobrevivem a restart)
            linha.add_item(botao_ok)
            linha.add_item(botao_nao)
            componentes.append(linha)
            if bloquear_decisao:
                componentes.append(
                    discord.ui.TextDisplay(
                        "-# Aprovar e Reprovar liberam após "
                        "**Registrar Pagamento** com o print do comprovante."
                    )
                )

        componentes.append(discord.ui.TextDisplay(_rodape(guild)))
        cor = discord.Color.dark_gold()
        if modo == "final":
            cor = discord.Color.green()
        self.add_item(discord.ui.Container(*componentes, accent_color=cor))

    async def _checar_perm(self, interacao: discord.Interaction) -> bool:
        membro = interacao.user
        if not isinstance(membro, discord.Member) or not _instrutor_ou_diretoria(
            membro
        ):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas **Instrutor** ou **Diretoria**."],
            )
            return False
        return True

    async def _ao_abrir_select_aprovar(self, interacao: discord.Interaction):
        if not await self._checar_perm(interacao):
            return
        chaves = self.chaves_cursos or await self._carregar_chaves()
        await editar_mensagem_original(
            interacao,
            view=ViewDecisaoCurso(
                titulo=self.titulo,
                corpo=self.corpo,
                guild=self.guild_ref,
                solicitacao_id=self.solicitacao_id,
                url_avatar=self.url_avatar,
                modo="selecionar_aprovar",
                chaves_cursos=chaves,
            ),
        )

    async def _ao_abrir_select_reprovar(self, interacao: discord.Interaction):
        if not await self._checar_perm(interacao):
            return
        chaves = self.chaves_cursos or await self._carregar_chaves()
        await editar_mensagem_original(
            interacao,
            view=ViewDecisaoCurso(
                titulo=self.titulo,
                corpo=self.corpo,
                guild=self.guild_ref,
                solicitacao_id=self.solicitacao_id,
                url_avatar=self.url_avatar,
                modo="selecionar_reprovar",
                chaves_cursos=chaves,
            ),
        )

    async def _carregar_chaves(self) -> list[str]:
        registro = await obter_solicitacao_curso(self.solicitacao_id)
        if registro is None:
            return []
        return parse_chaves_json(registro.chaves_cursos_json, registro.chave_curso)

    async def _ao_cancelar_confirmacao(self, interacao: discord.Interaction):
        if not await self._checar_perm(interacao):
            return
        chaves = self.chaves_cursos or await self._carregar_chaves()
        await editar_mensagem_original(
            interacao,
            view=ViewDecisaoCurso(
                titulo=self.titulo,
                corpo=self.corpo,
                guild=self.guild_ref,
                solicitacao_id=self.solicitacao_id,
                url_avatar=self.url_avatar,
                modo="normal",
                chaves_cursos=chaves,
            ),
        )

    async def _ao_select_aprovar(self, interacao: discord.Interaction):
        if not await self._checar_perm(interacao):
            return
        marcados = list((interacao.data or {}).get("values") or [])
        todas = self.chaves_cursos or await self._carregar_chaves()
        aprovadas = [
            chave_do_curso for chave_do_curso in todas if chave_do_curso in marcados
        ]
        reprovadas = [
            chave_do_curso for chave_do_curso in todas if chave_do_curso not in marcados
        ]
        await self._aplicar_decisao_parcial(interacao, aprovadas, reprovadas)

    async def _ao_select_reprovar(self, interacao: discord.Interaction):
        if not await self._checar_perm(interacao):
            return
        marcados = list((interacao.data or {}).get("values") or [])
        todas = self.chaves_cursos or await self._carregar_chaves()
        reprovadas = [
            chave_do_curso for chave_do_curso in todas if chave_do_curso in marcados
        ]
        aprovadas = [
            chave_do_curso for chave_do_curso in todas if chave_do_curso not in marcados
        ]
        await self._aplicar_decisao_parcial(interacao, aprovadas, reprovadas)

    async def _aplicar_decisao_parcial(
        self,
        interacao: discord.Interaction,
        aprovadas: list[str],
        reprovadas: list[str],
        observacao_decisao: str | None = None,
    ):
        membro = interacao.user
        assert isinstance(membro, discord.Member)
        try:
            if not interacao.response.is_done():
                await interacao.response.defer(ephemeral=True)
            registro = await decidir_cursos_parciais(
                solicitacao_id=self.solicitacao_id,
                chaves_aprovadas=aprovadas,
                chaves_reprovadas=reprovadas,
                instrutor_id=membro.id,
                observacao_decisao=observacao_decisao,
            )
            if registro is None:
                await responder_erro(
                    interacao,
                    titulo="Pedido não encontrado",
                    linhas=[f"`#{self.solicitacao_id}`"],
                )
                return
            if registro.status not in ("APROVADO", "REPROVADO"):
                await responder_aviso(
                    interacao,
                    titulo="Estado inesperado",
                    linhas=[f"Status: `{registro.status}`"],
                    delay=10,
                )
                return

            guilda = interacao.guild or self.guild_ref
            aluno = guilda.get_member(registro.discord_id) if guilda else None

            if aprovadas and aluno is not None:
                ok, detalhe = await conceder_cargos_dos_cursos(aluno, aprovadas)
                if not ok:
                    await enviar_erro_para_log_erros(
                        guilda,
                        "Curso aprovado parcialmente mas falha ao dar cargo",
                        RuntimeError(detalhe),
                        contexto="ViewDecisaoCurso.conceder",
                        usuario=membro,
                    )
                # Pedidos legados pagos 100% em moedas: instrutor recebe
                # a fatia proporcional. No fluxo novo (IN_GAME + desconto)
                # a receita é in-game e não vira moeda para o instrutor.
                if (
                    registro.forma_pagamento == "MOEDAS"
                    and registro.moedas_debitadas
                ):
                    valor_total = soma_valor_ingame(
                        parse_chaves_json(
                            registro.chaves_cursos_json,
                            registro.chave_curso,
                        )
                    )
                    valor_aprov = soma_valor_ingame(aprovadas)
                    if valor_total > 0 and valor_aprov > 0:
                        moedas_credito = max(
                            1,
                            int(
                                registro.moedas_debitadas
                                * valor_aprov
                                / valor_total
                            ),
                        )
                        await creditar_moedas_instrutor(
                            membro.id,
                            moedas_credito,
                        )

            if aprovadas:
                await publicar_resultado_final(
                    guilda,
                    registro=registro,
                    aluno=aluno,
                    staff=membro,
                    chaves=aprovadas,
                    aprovado=True,
                )
            if reprovadas:
                await publicar_resultado_final(
                    guilda,
                    registro=registro,
                    aluno=aluno,
                    staff=membro,
                    chaves=reprovadas,
                    aprovado=False,
                )

            from src.utils.notificacao import notificar_dm_curso_resultado

            await notificar_dm_curso_resultado(
                aluno=aluno,
                aprovadas=aprovadas,
                reprovadas=reprovadas,
                solicitacao_id=registro.id,
                staff=membro,
                guilda=guilda,
            )

            resumo = (
                f"Aprovados: "
                f"{', '.join(rotulo_curso(chave_do_curso) for chave_do_curso in aprovadas) or '—'}\n"
                f"Reprovados: "
                f"{', '.join(rotulo_curso(chave_do_curso) for chave_do_curso in reprovadas) or '—'}"
            )
            if observacao_decisao:
                resumo += f"\nObs. decisão: {observacao_decisao}"

            # Resultado já foi para aprovados/reprovados. Apaga o card
            # da fila de decisão (id no banco) para os pendentes no fim.
            await apagar_card_do_pedido(
                guilda,
                registro.id,
                mensagem_fallback=interacao.message,
            )

            await responder_sucesso(
                interacao,
                titulo="Decisão registrada",
                linhas=[resumo],
                delay=15,
            )
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro ao decidir curso (parcial)",
                erro,
                contexto="ViewDecisaoCurso._aplicar_decisao_parcial",
                usuario=membro,
            )
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=["Falha na decisão. Veja LOG_ERROS."],
            )


async def _apagar_mensagem_segura(
    mensagem: discord.Message | None,
) -> None:
    """Apaga uma mensagem do Discord sem interromper o fluxo principal."""
    if mensagem is None:
        return
    try:
        await mensagem.delete()
    except (discord.NotFound, discord.HTTPException) as erro_ao_apagar:
        ignorar_falha_cosmetica(
            erro_ao_apagar,
            o_que_falhou="apagar mensagem antiga de curso",
        )


async def apagar_card_do_pedido(
    guilda: discord.Guild | None,
    solicitacao_id: int,
    *,
    mensagem_fallback: discord.Message | None = None,
) -> None:
    """Apaga o card do pedido usando o id gravado no banco.

    Se o banco tiver canal + mensagem, busca e apaga. Se não achar, tenta
    a mensagem da interação (mesmo uptime). No fim limpa os ids no banco
    para não apontar para mensagem inexistente.
    """
    mensagem = mensagem_fallback
    registro = await obter_solicitacao_curso(solicitacao_id)
    if (
        registro is not None
        and registro.mensagem_id
        and registro.mensagem_canal_id
        and guilda is not None
    ):
        canal = guilda.get_channel(int(registro.mensagem_canal_id))
        if canal is not None:
            try:
                mensagem = await canal.fetch_message(int(registro.mensagem_id))
            except (discord.NotFound, discord.HTTPException) as erro:
                ignorar_falha_cosmetica(
                    erro,
                    o_que_falhou=(
                        "buscar mensagem do pedido no banco "
                        f"(#{solicitacao_id})"
                    ),
                )
                # Mantém o fallback da interação se a busca falhou
                if mensagem is None:
                    mensagem = mensagem_fallback
    await _apagar_mensagem_segura(mensagem)
    await limpar_mensagem_solicitacao_curso(solicitacao_id)


async def publicar_no_agendamentos(
    guilda: discord.Guild | None,
    *,
    membro: discord.Member,
    registro,
) -> bool:
    """Publica no canal de agendamentos o card que instrutores podem aceitar.

    Retorna falso quando a guilda ou o canal não estão disponíveis e envia o
    detalhe ao log de erros. Quando a publicação funciona, grava no banco os
    identificadores do canal e da mensagem para futuras atualizações.
    """
    if guilda is None:
        return False
    canal_id = CANAIS.get("CANAL_AGENDAMENTOS_DE_CURSO")
    canal = guilda.get_channel(int(canal_id)) if canal_id else None
    if canal is None:
        await enviar_erro_para_log_erros(
            guilda,
            "CANAL_AGENDAMENTOS_DE_CURSO não encontrado",
            RuntimeError(f"id={canal_id}"),
            contexto="publicar_no_agendamentos",
            usuario=membro,
        )
        return False

    titulo, corpo = montar_linhas_corpo_pedido(membro=membro, registro=registro)
    view = ViewAceitarAgendamento(
        titulo=titulo,
        corpo=corpo,
        guild=guilda,
        solicitacao_id=registro.id,
        url_avatar=membro.display_avatar.url,
    )
    try:
        mensagem = await canal.send(view=view)
        await marcar_mensagem_solicitacao_curso(registro.id, canal.id, mensagem.id)
        return True
    except discord.HTTPException as erro:
        await enviar_erro_para_log_erros(
            guilda,
            "Falha ao postar agendamento de curso",
            erro,
            contexto="publicar_no_agendamentos.send",
            usuario=membro,
        )
        return False


async def atualizar_ou_publicar_agendamento(
    guilda: discord.Guild | None,
    *,
    membro: discord.Member,
    registro,
) -> bool:
    """Republica o card pendente no fim do canal (apaga o antigo se existir).

    Assim os pedidos ainda em aberto ficam sempre abaixo dos já tratados,
    facilitando achar o próximo a aceitar. O id da mensagem nova é gravado
    no banco para sobreviver a restart e permitir apagar de novo depois.
    """
    if guilda is None:
        return False

    await apagar_card_do_pedido(guilda, registro.id)

    return await publicar_no_agendamentos(
        guilda,
        membro=membro,
        registro=registro,
    )


async def publicar_para_decisao(
    guilda: discord.Guild | None,
    *,
    registro,
    aluno: discord.Member | None,
) -> bool:
    """Envia o pedido aceito ao canal onde cursos serão aprovados ou reprovados.

    Usa o aluno conhecido ou um substituto mínimo quando ele não está em cache,
    preservando menção e avatar quando possível. Grava no banco o canal e o
    id da mensagem do card para apagar/republicar depois de um restart.
    Os botões usam custom_id fixo e o on_interaction do cog reconstrói a
    ação sem depender da view em memória.
    """
    if guilda is None:
        return False
    canal_id = CANAIS.get("CANAL_APROVAR_REPROVAR_CURSO")
    canal = guilda.get_channel(int(canal_id)) if canal_id else None
    if canal is None:
        await enviar_erro_para_log_erros(
            guilda,
            "CANAL_APROVAR_REPROVAR_CURSO não encontrado",
            RuntimeError(f"id={canal_id}"),
            contexto="publicar_para_decisao",
        )
        return False

    membro_ref = aluno or guilda.get_member(registro.discord_id)
    if membro_ref is None:

        class _Fake:
            mention = f"<@{registro.discord_id}>"
            id = registro.discord_id
            display_avatar = type("A", (), {"url": None})()

        membro_ref = _Fake()  # type: ignore

    titulo, corpo = montar_linhas_corpo_pedido(
        membro=membro_ref,  # type: ignore[arg-type]
        registro=registro,
    )
    corpo += _bloco_observacao_instrutor(guilda, registro)
    url = getattr(getattr(membro_ref, "display_avatar", None), "url", None)
    chaves = parse_chaves_json(registro.chaves_cursos_json, registro.chave_curso)
    forma = registro.forma_pagamento or ""
    precisa_repasse = forma in ("IN_GAME", "IN_GAME_COM_DESCONTO")
    completo = bool(getattr(registro, "repasse_registrado", False))
    bloquear = precisa_repasse and not completo
    mostrar_repasse = precisa_repasse and not completo
    try:
        mensagem = await canal.send(
            view=ViewDecisaoCurso(
                titulo=titulo,
                corpo=corpo,
                guild=guilda,
                solicitacao_id=registro.id,
                url_avatar=url,
                modo="normal",
                chaves_cursos=chaves,
                bloquear_decisao=bloquear,
                mostrar_botao_repasse=mostrar_repasse,
            )
        )
        await marcar_mensagem_solicitacao_curso(
            registro.id,
            canal.id,
            mensagem.id,
        )
        return True
    except discord.HTTPException as erro:
        await enviar_erro_para_log_erros(
            guilda,
            "Falha ao postar decisão de curso",
            erro,
            contexto="publicar_para_decisao.send",
        )
        return False


async def publicar_resultado_final(
    guilda: discord.Guild | None,
    *,
    registro,
    aluno: discord.Member | None,
    staff: discord.Member,
    chaves: list[str],
    aprovado: bool,
) -> None:
    """Card final com thumbnail do instrutor (aprovados / reprovados)."""
    if guilda is None:
        return
    canal_id = (
        CANAIS.get("CANAL_APROVADOS_CURSOS")
        if aprovado
        else CANAIS.get("CANAL_REPROVADOS_CURSOS")
    )
    canal = guilda.get_channel(int(canal_id)) if canal_id else None
    if canal is None:
        return

    if aprovado:
        titulo = "📝 Curso Aprovado" if len(chaves) <= 1 else "📝 Cursos Aprovados"
    else:
        titulo = "📝 Curso Reprovado" if len(chaves) <= 1 else "📝 Cursos Reprovados"

    mencao_aluno = aluno.mention if aluno else f"<@{registro.discord_id}>"
    if len(chaves) <= 1:
        chave = chaves[0] if chaves else registro.chave_curso
        bloco_cursos = (
            f"**{'🌄 Curso aplicado' if aprovado else 'Curso'}:** "
            f"{menção_cargo_curso(chave)}"
        )
    else:
        lista = "\n".join(
            f"> {menção_cargo_curso(chave_do_curso)}" for chave_do_curso in chaves
        )
        bloco_cursos = (
            f"**📚 Cursos {'aplicados' if aprovado else 'reprovados'}:**\n{lista}"
        )

    corpo = (
        f"**👤 Aluno:** {mencao_aluno} | **📋 Pedido:** `#{registro.id}`\n"
        f"**🛡️ Instrutor responsável:** {staff.mention}\n\n"
        f"{bloco_cursos}\n"
        f"**💳 Forma de pagamento:** `{registro.forma_pagamento}`"
    )

    momento = int(datetime.now(timezone.utc).timestamp())
    rodape = f"-# {guilda.name} • <t:{momento}:f>"
    url_instrutor = staff.display_avatar.url

    componentes = [
        discord.ui.TextDisplay(f"# {titulo}"),
        discord.ui.Section(
            corpo,
            accessory=discord.ui.Thumbnail(url_instrutor),
        ),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(rodape),
    ]
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            *componentes,
            accent_color=discord.Color.green() if aprovado else discord.Color.red(),
        )
    )
    try:
        await canal.send(view=view)
    except discord.HTTPException as erro:
        await enviar_erro_para_log_erros(
            guilda,
            "Falha ao postar resultado de curso",
            erro,
            contexto="publicar_resultado_final",
            usuario=staff,
        )


async def montar_view_decisao_a_partir_do_banco(
    guilda: discord.Guild,
    solicitacao_id: int,
    *,
    modo: str = "normal",
) -> ViewDecisaoCurso | None:
    """
    Reconstroi o card de decisao a partir do banco (apos restart).

    Usado pelo on_interaction para editar a mensagem sem depender da view
    que existia na memoria antes do deploy.
    """
    registro = await obter_solicitacao_curso(solicitacao_id)
    if registro is None:
        return None
    aluno = guilda.get_member(registro.discord_id)
    membro_ref = aluno
    if membro_ref is None:

        class _Fake:
            mention = f"<@{registro.discord_id}>"
            id = registro.discord_id
            display_avatar = type("A", (), {"url": None})()

        membro_ref = _Fake()  # type: ignore

    titulo, corpo = montar_linhas_corpo_pedido(
        membro=membro_ref,  # type: ignore[arg-type]
        registro=registro,
    )
    corpo += _bloco_observacao_instrutor(guilda, registro)
    url = getattr(getattr(membro_ref, "display_avatar", None), "url", None)
    chaves = parse_chaves_json(registro.chaves_cursos_json, registro.chave_curso)
    forma = registro.forma_pagamento or ""
    precisa_repasse = forma in ("IN_GAME", "IN_GAME_COM_DESCONTO")
    completo = bool(getattr(registro, "repasse_registrado", False))
    bloquear = precisa_repasse and not completo
    mostrar_repasse = precisa_repasse and not completo
    return ViewDecisaoCurso(
        titulo=titulo,
        corpo=corpo,
        guild=guilda,
        solicitacao_id=registro.id,
        url_avatar=url,
        modo=modo,
        chaves_cursos=chaves,
        bloquear_decisao=bloquear if modo == "normal" else False,
        mostrar_botao_repasse=mostrar_repasse if modo == "normal" else False,
    )


async def processar_clique_aceitar_curso(
    interacao: discord.Interaction,
    solicitacao_id: int,
) -> None:
    """Abre o modal de aceitar agendamento (instrutor/diretoria)."""
    membro = interacao.user
    if not isinstance(membro, discord.Member) or not _instrutor_ou_diretoria(membro):
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=["Apenas **Instrutor** ou **Diretoria** pode aceitar."],
        )
        return
    registro = await obter_solicitacao_curso(solicitacao_id)
    if registro is not None and registro.status != "AGENDADO":
        await responder_aviso(
            interacao,
            titulo="Pedido indisponível",
            linhas=[f"Status atual: `{registro.status}`."],
            delay=10,
        )
        return
    await interacao.response.send_modal(
        ModalObservacaoInstrutor(
            solicitacao_id=solicitacao_id,
            instrutor_id=membro.id,
            mensagem_agendamento=interacao.message,
        )
    )


async def processar_clique_recusar_curso(
    interacao: discord.Interaction,
    solicitacao_id: int,
) -> None:
    """Abre o modal para recusar um pedido ainda em agendamento."""
    membro = interacao.user
    if not isinstance(membro, discord.Member) or not _instrutor_ou_diretoria(membro):
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=["Apenas **Instrutor** ou **Diretoria** pode recusar."],
        )
        return
    registro = await obter_solicitacao_curso(solicitacao_id)
    if registro is None:
        await responder_erro(
            interacao,
            titulo="Pedido não encontrado",
            linhas=[f"`#{solicitacao_id}`"],
        )
        return
    if registro.status != "AGENDADO":
        await responder_aviso(
            interacao,
            titulo="Pedido indisponível",
            linhas=[f"Status atual: `{registro.status}`."],
            delay=10,
        )
        return
    await interacao.response.send_modal(
        ModalRecusarAgendamento(
            solicitacao_id=solicitacao_id,
            instrutor_id=membro.id,
            mensagem_agendamento=interacao.message,
        )
    )


class ModalRecusarAgendamento(LoggingModalMixin, discord.ui.Modal):
    """Motivo opcional ao recusar pedido no canal de agendamentos."""

    def __init__(
        self,
        *,
        solicitacao_id: int,
        instrutor_id: int,
        mensagem_agendamento: discord.Message | None,
    ):
        super().__init__(title="Recusar solicitação")
        self.solicitacao_id = solicitacao_id
        self.instrutor_id = instrutor_id
        self.mensagem_agendamento = mensagem_agendamento
        self.campo = discord.ui.TextInput(
            label="Motivo (opcional)",
            style=discord.TextStyle.paragraph,
            placeholder="Ex.: Data inválida / curso errado / aluno sem requisito",
            required=False,
            max_length=500,
        )
        self.add_item(self.campo)

    async def on_submit(self, interacao: discord.Interaction):
        try:
            await interacao.response.defer(ephemeral=True)
            motivo = (self.campo.value or "").strip()
            registro = await recusar_agendamento(
                solicitacao_id=self.solicitacao_id,
                instrutor_id=self.instrutor_id,
                motivo=motivo,
            )
            if registro is None:
                await responder_erro(
                    interacao,
                    titulo="Pedido não encontrado",
                    linhas=[f"ID `#{self.solicitacao_id}`."],
                )
                return
            if registro.status != "CANCELADO":
                await responder_aviso(
                    interacao,
                    titulo="Não foi possível recusar",
                    linhas=[f"Status atual: `{registro.status}`."],
                    delay=10,
                )
                return

            # Devolve moedas de desconto, se houver
            moedas = int(registro.moedas_debitadas or 0)
            if moedas > 0:
                await creditar_moedas_instrutor(registro.discord_id, moedas)

            guilda = interacao.guild
            aluno = guilda.get_member(registro.discord_id) if guilda else None

            # Remove o card do canal de agendamentos (id no banco + fallback)
            await apagar_card_do_pedido(
                guilda,
                registro.id,
                mensagem_fallback=self.mensagem_agendamento,
            )

            if aluno is not None:
                linhas_dm = [
                    f"Seu pedido `#{registro.id}` foi **recusado**.",
                    f"Por: {interacao.user.mention}",
                ]
                if motivo:
                    linhas_dm.append(f"Motivo: {motivo}")
                if moedas > 0:
                    linhas_dm.append(
                        f"As **{moedas}** moeda(s) de desconto foram devolvidas."
                    )
                await enviar_dm_card(
                    aluno,
                    titulo="Pedido de curso recusado",
                    linhas=linhas_dm,
                    cor=discord.Color.red(),
                    guilda=guilda,
                )

            await responder_sucesso(
                interacao,
                titulo="Solicitação recusada",
                linhas=[
                    f"Pedido `#{registro.id}` cancelado.",
                    *(
                        [f"**{moedas}** moeda(s) devolvidas ao aluno."]
                        if moedas
                        else []
                    ),
                ],
                delay=15,
            )
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro ao recusar agendamento de curso",
                erro,
                contexto="ModalRecusarAgendamento.on_submit",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=["Falha ao recusar. Veja LOG_ERROS."],
            )


async def processar_clique_abrir_decisao(
    interacao: discord.Interaction,
    solicitacao_id: int,
    modo: str,
) -> None:
    """Troca o card para o select de aprovar ou reprovar."""
    membro = interacao.user
    if not isinstance(membro, discord.Member) or not _instrutor_ou_diretoria(membro):
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=["Apenas **Instrutor** ou **Diretoria**."],
        )
        return
    if interacao.guild is None:
        await responder_erro(
            interacao,
            titulo="Contexto inválido",
            linhas=["Use este botão dentro do servidor."],
        )
        return

    registro = await obter_solicitacao_curso(solicitacao_id)
    if registro is None:
        await responder_erro(
            interacao,
            titulo="Pedido não encontrado",
            linhas=[f"`#{solicitacao_id}`"],
        )
        return

    # Instrutor que aceitou, ou Diretoria em intervenção
    if not _pode_decidir_pedido(membro, registro):
        await responder_erro(
            interacao,
            titulo="Sem permissão neste pedido",
            linhas=[
                "Somente quem **aceitou** esta solicitação "
                "(ou a **Diretoria**) pode aprovar ou reprovar.",
            ],
        )
        return

    # Aprovar e reprovar exigem repasse (aluno paga mesmo se reprovado)
    if modo in ("selecionar_aprovar", "selecionar_reprovar"):
        forma = registro.forma_pagamento or ""
        precisa_repasse = forma in ("IN_GAME", "IN_GAME_COM_DESCONTO")
        if precisa_repasse and not getattr(registro, "repasse_registrado", False):
            acao = "aprovar" if modo == "selecionar_aprovar" else "reprovar"
            # Cards antigos podem não ter o botão Registrar Pagamento:
            # apaga (id no banco) e republica no fim com o botão atual.
            aluno_para_card = interacao.guild.get_member(registro.discord_id)
            await apagar_card_do_pedido(
                interacao.guild,
                solicitacao_id,
                mensagem_fallback=interacao.message,
            )
            await publicar_para_decisao(
                interacao.guild,
                registro=registro,
                aluno=aluno_para_card,
            )
            await responder_aviso(
                interacao,
                titulo="Repasse pendente",
                linhas=[
                    f"Antes de **{acao}**, registre o pagamento do "
                    "repasse ao hospital.",
                    "Mesmo em caso de reprovação o aluno precisa pagar.",
                    "O card foi republicado no fim do canal com o botão "
                    "**Registrar Pagamento**.",
                    "Clique nele e envie o print do comprovante neste canal.",
                ],
                delay=25,
            )
            return

    view = await montar_view_decisao_a_partir_do_banco(
        interacao.guild,
        solicitacao_id,
        modo=modo,
    )
    if view is None:
        await responder_erro(
            interacao,
            titulo="Pedido não encontrado",
            linhas=[f"`#{solicitacao_id}`"],
        )
        return
    await editar_mensagem_original(interacao, view=view)


async def processar_registrar_repasse_curso(
    interacao: discord.Interaction,
    solicitacao_id: int,
) -> None:
    """
    Pede comprovante do repasse no canal de decisão e publica no
    REGISTRAR_CURSO_PRATICOS (sem DM).
    """
    from src.financas.financas_views import (
        _primeiro_anexo_valido,
        anexo_e_comprovante_valido,
    )
    from src.utils.mensagens import responder_info

    membro = interacao.user
    if not isinstance(membro, discord.Member) or not _instrutor_ou_diretoria(membro):
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=["Apenas **Instrutor** ou **Diretoria**."],
        )
        return

    registro = await obter_solicitacao_curso(solicitacao_id)
    if registro is None:
        await responder_erro(
            interacao,
            titulo="Pedido não encontrado",
            linhas=[f"`#{solicitacao_id}`"],
        )
        return

    if registro.forma_pagamento == "GRATUITO":
        await responder_aviso(
            interacao,
            titulo="Pedido gratuito",
            linhas=["Não há repasse in-game neste pedido."],
            delay=12,
        )
        return

    if getattr(registro, "repasse_registrado", False):
        await responder_aviso(
            interacao,
            titulo="Já registrado",
            linhas=["Todos os comprovantes deste pedido já foram enviados."],
            delay=10,
        )
        return

    # Instrutor que aceitou, ou Diretoria em intervenção
    if not _pode_decidir_pedido(membro, registro):
        await responder_erro(
            interacao,
            titulo="Sem permissão neste pedido",
            linhas=[
                "Só quem **aceitou** o agendamento "
                "(ou a **Diretoria**) registra o repasse.",
            ],
        )
        return

    grupo = proximo_grupo_repasse_pendente(registro)
    if grupo is None:
        await responder_aviso(
            interacao,
            titulo="Nada pendente",
            linhas=["Não há grupo de repasse aguardando comprovante."],
            delay=10,
        )
        return

    if not interacao.response.is_done():
        await interacao.response.defer(ephemeral=True)

    minutos = PRAZO_COMPROVANTE_REPASSE_SEGUNDOS // 60
    mensagem_pedido_comprovante = await responder_info(
        interacao,
        titulo="Comprovante do repasse",
        linhas=[
            f"**Grupo:** {grupo['rotulo']}",
            f"**Repasse ao hospital:** "
            f"`{formatar_reais(int(grupo['repasse']))}`",
            f"**Valor pago in-game (grupo):** "
            f"`{formatar_reais(int(grupo['valor_pago']))}`",
            "Envie **neste canal** o print do comprovante.",
            "Formatos: **PNG, JPG, WEBP, GIF ou PDF**.",
            f"Prazo: **{minutos} minutos**.",
            "Só conta mensagem **sua** com **anexo válido**.",
        ],
        delay=None,
    )

    bot = interacao.client
    canal_id = interacao.channel_id
    autor_id = membro.id

    def checagem(mensagem: discord.Message) -> bool:
        if mensagem.author.id != autor_id:
            return False
        if mensagem.channel.id != canal_id:
            return False
        return _primeiro_anexo_valido(mensagem) is not None

    try:
        mensagem_comprovante = await bot.wait_for(
            "message",
            timeout=PRAZO_COMPROVANTE_REPASSE_SEGUNDOS,
            check=checagem,
        )
    except TimeoutError:
        await responder_aviso(
            interacao,
            titulo="Prazo esgotado",
            linhas=[
                f"Nenhum comprovante em **{minutos} minutos**.",
                "Clique de novo em **Registrar Pagamento** quando tiver o print.",
            ],
            delay=20,
        )
        return

    anexo = _primeiro_anexo_valido(mensagem_comprovante)
    if anexo is None or not anexo_e_comprovante_valido(anexo):
        await responder_erro(
            interacao,
            titulo="Comprovante inválido",
            linhas=["Envie imagem ou PDF e tente de novo."],
        )
        return

    nome_arquivo = anexo.filename or "comprovante_repasse.png"
    try:
        bytes_do_arquivo = await anexo.read()
    except (discord.HTTPException, OSError) as erro_leitura:
        await enviar_erro_para_log_erros(
            interacao.guild,
            "Falha ao baixar comprovante de repasse de curso",
            erro_leitura,
            contexto="processar_registrar_repasse_curso.read",
            usuario=membro,
        )
        await responder_erro(
            interacao,
            titulo="Falha no comprovante",
            linhas=["Não consegui ler o anexo. Envie de novo."],
        )
        return

    if not bytes_do_arquivo:
        await responder_erro(
            interacao,
            titulo="Arquivo vazio",
            linhas=["O anexo veio sem conteúdo."],
        )
        return

    guilda = interacao.guild
    if guilda is None:
        return

    chave_canal = grupo["canal_chave"]
    canal_destino_id = CANAIS.get(chave_canal) or 0
    canal_destino = guilda.get_channel(int(canal_destino_id))
    if canal_destino is None:
        await responder_erro(
            interacao,
            titulo="Canal não configurado",
            linhas=[f"`{chave_canal}` ausente ou inválido."],
        )
        return

    aluno = guilda.get_member(registro.discord_id)
    mencao_aluno = aluno.mention if aluno else f"<@{registro.discord_id}>"
    valor_pago_txt = formatar_reais(int(grupo["valor_pago"]))
    repasse_txt = formatar_reais(int(grupo["repasse"]))
    moedas = int(registro.moedas_debitadas or 0)
    cotacao = int(getattr(registro, "cotacao_moeda", 0) or 0)
    linhas_cursos = []
    for chave in grupo["chaves"]:
        linhas_cursos.append(f"> {menção_cargo_curso(chave)}")
    bloco_cursos = "\n".join(linhas_cursos) if linhas_cursos else "> —"

    instrutor_responsavel = _mencao_instrutor(guilda, registro.instrutor_id)
    momento = int(datetime.now(timezone.utc).timestamp())
    titulo_card_repasse = (
        "# 📝 Repasse de Curso Prático"
        if grupo["id"] == "praticos"
        else f"# 📝 Repasse — {grupo['rotulo']}"
    )

    texto_card = (
        f"{titulo_card_repasse}\n"
        f"**👤 Aluno:** {mencao_aluno} | **📋 Pedido:** `#{registro.id}`\n"
        f"**🛡️ Instrutor responsável:** {instrutor_responsavel}\n"
        f"**🌄 Curso(s):**\n"
        f"{bloco_cursos}\n"
        f"**💳 Forma de pagamento:** `{registro.forma_pagamento}`\n"
        f"**Valor pago in-game (grupo):** `{valor_pago_txt}`\n"
        f"**Repasse ao hospital:** `{repasse_txt}`\n"
    )
    if moedas > 0 and grupo["id"] == "praticos":
        texto_card += (
            f"**Desconto do pedido:** `{moedas}` moeda(s) "
            f"({formatar_reais(cotacao)} cada)\n"
        )
    texto_card += f"**Status do repasse:** registrado por {membro.mention}"

    import io

    # Cópia local dos bytes — envio independente do CDN do Discord
    buffer_anexo = io.BytesIO(bytes_do_arquivo)
    buffer_anexo.seek(0)
    arquivo = discord.File(fp=buffer_anexo, filename=nome_arquivo)

    e_imagem = nome_arquivo.lower().endswith(
        (".png", ".jpg", ".jpeg", ".webp", ".gif")
    )
    componentes_card: list = [
        discord.ui.TextDisplay(texto_card),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
    ]
    if e_imagem:
        # Components V2: galeria aponta para o anexo enviado no mesmo send
        componentes_card.append(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(f"attachment://{nome_arquivo}")
            )
        )
    else:
        # PDF / outros: componente File no card
        try:
            componentes_card.append(
                discord.ui.File(f"attachment://{nome_arquivo}")
            )
        except (TypeError, AttributeError):
            pass
    componentes_card.append(
        discord.ui.TextDisplay(
            f"-# CENTRO MÉDICO SUL VALLEY • <t:{momento}:f>"
        )
    )

    try:
        view_log = discord.ui.LayoutView(timeout=None)
        view_log.add_item(
            discord.ui.Container(
                *componentes_card,
                accent_color=discord.Color.dark_teal(),
            )
        )
        await canal_destino.send(view=view_log, file=arquivo)
    except discord.HTTPException as erro_envio:
        # Fallback: mensagem clássica só com o arquivo + texto
        try:
            buffer_fallback = io.BytesIO(bytes_do_arquivo)
            buffer_fallback.seek(0)
            arquivo_fallback = discord.File(
                fp=buffer_fallback,
                filename=nome_arquivo,
            )
            texto_simples = (
                f"**📝 Repasse — {grupo['rotulo']}** · "
                f"Pedido `#{registro.id}`\n"
                f"Aluno: {mencao_aluno} · Instrutor: {instrutor_responsavel}\n"
                f"Forma: `{registro.forma_pagamento}` · "
                f"Pago: `{valor_pago_txt}` · "
                f"Repasse: `{repasse_txt}` · por {membro.mention}"
            )
            await canal_destino.send(
                content=texto_simples,
                file=arquivo_fallback,
            )
        except discord.HTTPException as erro_fallback:
            await enviar_erro_para_log_erros(
                guilda,
                "Falha ao postar comprovante de repasse",
                erro_fallback,
                contexto="processar_registrar_repasse_curso.send",
                usuario=membro,
            )
            await responder_erro(
                interacao,
                titulo="Falha ao publicar",
                linhas=[
                    "Não consegui enviar o comprovante ao canal de registro.",
                ],
            )
            return

    registro_atualizado = await marcar_grupo_repasse(
        solicitacao_id,
        grupo["id"],
    )
    completo = bool(
        registro_atualizado
        and getattr(registro_atualizado, "repasse_registrado", False)
    )

    # Apaga o print e o aviso no canal de decisão
    await _apagar_mensagem_segura(mensagem_comprovante)
    await _apagar_mensagem_segura(mensagem_pedido_comprovante)

    # Republica o card no fim do canal com botões atualizados
    # (inclui Registrar Pagamento se ainda faltar grupo, ou libera
    # Aprovar/Reprovar quando o repasse está completo).
    # Apaga pelo id no banco e grava o id da mensagem nova.
    aluno_para_card = guilda.get_member(registro.discord_id)
    await apagar_card_do_pedido(
        guilda,
        solicitacao_id,
        mensagem_fallback=interacao.message,
    )
    if registro_atualizado is not None:
        await publicar_para_decisao(
            guilda,
            registro=registro_atualizado,
            aluno=aluno_para_card,
        )

    if completo:
        linhas_ok = [
            f"Grupo **{grupo['rotulo']}** registrado "
            f"(repasse `{repasse_txt}`).",
            "Todos os comprovantes deste pedido estão ok.",
            "Aprovar e Reprovar estão **liberados**.",
        ]
    else:
        linhas_ok = [
            f"Grupo **{grupo['rotulo']}** registrado "
            f"(repasse `{repasse_txt}`).",
            "Ainda há curso(s) de área pendente(s).",
            "Clique de novo em **Registrar Pagamento** "
            "para o próximo comprovante.",
        ]
    await responder_sucesso(
        interacao,
        titulo="Repasse registrado",
        linhas=linhas_ok,
        delay=15,
    )


async def processar_clique_cancelar_decisao(
    interacao: discord.Interaction,
    solicitacao_id: int,
) -> None:
    """Volta o card ao modo normal (Aprovar / Reprovar)."""
    await processar_clique_abrir_decisao(
        interacao,
        solicitacao_id,
        modo="normal",
    )


async def processar_select_decisao_curso(
    interacao: discord.Interaction,
    solicitacao_id: int,
    modo: str,
) -> None:
    """Aplica a decisão parcial a partir do select (após restart ou no mesmo uptime)."""
    membro = interacao.user
    if not isinstance(membro, discord.Member) or not _instrutor_ou_diretoria(membro):
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=["Apenas **Instrutor** ou **Diretoria**."],
        )
        return
    if interacao.guild is None:
        await responder_erro(
            interacao,
            titulo="Contexto inválido",
            linhas=["Use este botão dentro do servidor."],
        )
        return

    registro = await obter_solicitacao_curso(solicitacao_id)
    if registro is None:
        await responder_erro(
            interacao,
            titulo="Pedido não encontrado",
            linhas=[f"`#{solicitacao_id}`"],
        )
        return
    if not _pode_decidir_pedido(membro, registro):
        await responder_erro(
            interacao,
            titulo="Sem permissão neste pedido",
            linhas=[
                "Somente quem **aceitou** esta solicitação "
                "(ou a **Diretoria**) pode aprovar ou reprovar.",
            ],
        )
        return

    view = await montar_view_decisao_a_partir_do_banco(
        interacao.guild,
        solicitacao_id,
        modo=modo,
    )
    if view is None:
        await responder_erro(
            interacao,
            titulo="Pedido não encontrado",
            linhas=[f"`#{solicitacao_id}`"],
        )
        return

    marcados = list((interacao.data or {}).get("values") or [])
    todas = view.chaves_cursos or await view._carregar_chaves()
    if modo == "selecionar_aprovar":
        aprovadas = [c for c in todas if c in marcados]
        reprovadas = [c for c in todas if c not in marcados]
    else:
        reprovadas = [c for c in todas if c in marcados]
        aprovadas = [c for c in todas if c not in marcados]

    # Observação da decisão é nova — não reutiliza a da aceitação
    await interacao.response.send_modal(
        ModalObservacaoDecisao(
            solicitacao_id=solicitacao_id,
            aprovadas=aprovadas,
            reprovadas=reprovadas,
            titulo_card=view.titulo,
            corpo_card=view.corpo,
            url_avatar=view.url_avatar,
            chaves_cursos=view.chaves_cursos,
            mensagem_decisao=interacao.message,
        )
    )


class ModalObservacaoDecisao(LoggingModalMixin, discord.ui.Modal):
    """
    Observação da aprovação/reprovação (independente da aceitação).
    """

    def __init__(
        self,
        *,
        solicitacao_id: int,
        aprovadas: list[str],
        reprovadas: list[str],
        titulo_card: str,
        corpo_card: str,
        url_avatar: str | None,
        chaves_cursos: list[str],
        mensagem_decisao: discord.Message | None,
    ):
        super().__init__(title="Observação da decisão")
        self.solicitacao_id = solicitacao_id
        self.aprovadas = list(aprovadas)
        self.reprovadas = list(reprovadas)
        self.titulo_card = titulo_card
        self.corpo_card = corpo_card
        self.url_avatar = url_avatar
        self.chaves_cursos = list(chaves_cursos)
        self.mensagem_decisao = mensagem_decisao
        self.campo = discord.ui.TextInput(
            label="Observação da decisão",
            style=discord.TextStyle.paragraph,
            placeholder="Ex.: Aplicado com sucesso / aluno não compareceu",
            required=True,
            max_length=400,
        )
        self.add_item(self.campo)

    async def on_submit(self, interacao: discord.Interaction):
        observacao = (self.campo.value or "").strip()
        guilda = interacao.guild
        if guilda is None:
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use este formulário dentro do servidor."],
            )
            return
        view_tmp = ViewDecisaoCurso(
            titulo=self.titulo_card,
            corpo=self.corpo_card,
            guild=guilda,
            solicitacao_id=self.solicitacao_id,
            url_avatar=self.url_avatar,
            modo="final",
            chaves_cursos=self.chaves_cursos,
        )
        await view_tmp._aplicar_decisao_parcial(
            interacao,
            self.aprovadas,
            self.reprovadas,
            observacao_decisao=observacao,
        )
        # _aplicar_decisao_parcial já apaga pelo id no banco. Se o modal
        # ainda tiver a mensagem (mesmo uptime), tenta de novo por segurança.
        await _apagar_mensagem_segura(self.mensagem_decisao)


def view_persistente_cursos() -> PainelCursosLayout:
    """Cria o painel sem prazo para sobreviver a reinicializações do bot."""
    return PainelCursosLayout()
