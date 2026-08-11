"""Painéis e botões do fluxo de cursos (solicitar → agendar → aceitar → decidir)."""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

import discord

from src.config import (
    CANAIS,
    VALOR_MOEDA_INGAME,
)
from src.cursos.cursos_service import (
    aceitar_agendamento,
    buscar_pedido_aberto,
    conceder_cargos_dos_cursos,
    creditar_moedas_instrutor,
    debitar_moedas_curso,
    decidir_cursos_parciais,
    listar_cursos_ordenados,
    marcar_mensagem_solicitacao_curso,
    membro_tem_curso,
    menção_cargo_curso,
    mesclar_cursos_no_pedido,
    moedas_necessarias_para_pacote,
    montar_linhas_corpo_pedido,
    obter_curso,
    obter_solicitacao_curso,
    parse_chaves_json,
    registrar_solicitacao_pacote,
    rotulo_curso,
    soma_valor_ingame,
)
from src.plantao.permissoes import e_diretoria
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
    enviar_erro_para_log_erros,
)
from src.utils.formatacao import formatar_reais
from src.utils.mensagens import (
    COR_SUCESSO,
    responder_aviso,
    responder_erro,
    responder_sucesso,
)
from src.utils.notificacao import enviar_dm_card

CUSTOM_ID_BOTAO_SELECIONAR = "cursos:botao_selecionar"
CUSTOM_ID_SELECT_MULTI = "cursos:select_multi"
CUSTOM_ID_ACEITAR = "cursos:aceitar:"
CUSTOM_ID_APROVAR = "cursos:aprovar:"
CUSTOM_ID_REPROVAR = "cursos:reprovar:"
CUSTOM_ID_CONFIRMA_APROVAR = "cursos:confirma_aprovar:"
CUSTOM_ID_CONFIRMA_REPROVAR = "cursos:confirma_reprovar:"
CUSTOM_ID_CANCELA_DECISAO = "cursos:cancela_decisao:"


def _instrutor_ou_diretoria(membro: discord.Member) -> bool:
    if e_diretoria(membro):
        return True
    # Instrutores: qualquer cargo cujo nome contenha Instrutor
    for cargo in membro.roles:
        nome = (cargo.name or "").lower()
        if "instrutor" in nome:
            return True
    return False


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
                    "✅ Pague com **moedas de plantão** ou registre **in-game**.\n"
                    "✅ Curso concluído = você recebe o **cargo** correspondente.\n"
                    "✅ Se já tiver um pedido aberto, novos cursos **entram no mesmo card**."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha_botoes,
                accent_color=discord.Color.dark_teal(),
            )
        )

    async def _ao_abrir_selecao(self, interacao: discord.Interaction):
        try:
            await interacao.response.send_message(
                view=SeletorMultiCursosView(interacao.user.id),
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
        if interacao.user.id != self.solicitante_id:
            await responder_erro(
                interacao,
                titulo="Não é seu pedido",
                linhas=["Só quem iniciou a solicitação pode confirmar."],
            )
            return
        observacao = (self.campo_observacao.value or "").strip()
        view = ConfirmacaoPagamentoPacoteView(
            chaves=self.chaves,
            solicitante_id=self.solicitante_id,
            observacao_aluno=observacao,
        )
        # Uma única mensagem efêmera de confirmação (substitui o fluxo anterior)
        await interacao.response.send_message(view=view, ephemeral=True)

        # Tenta apagar a mensagem do select (reduz spam)
        if self.id_mensagem_seletor is not None:
            try:
                await interacao.followup.delete_message(self.id_mensagem_seletor)
            except (discord.HTTPException, discord.NotFound):
                pass


class ConfirmacaoPagamentoPacoteView(LoggingViewMixin, discord.ui.LayoutView):
    """Confirma pagamento do pacote (moedas ou in-game)."""

    def __init__(
        self,
        *,
        chaves: list[str],
        solicitante_id: int,
        observacao_aluno: str,
    ):
        super().__init__(timeout=180)
        self.chaves = chaves
        self.solicitante_id = solicitante_id
        self.observacao_aluno = observacao_aluno

        valor_total = soma_valor_ingame(chaves)
        moedas = moedas_necessarias_para_pacote(chaves)

        lista = "\n".join(
            f"• {rotulo_curso(chave)} — {formatar_reais(int((obter_curso(chave) or {}).get('valor_ingame') or 0))}"
            for chave in chaves
        )
        obs_txt = observacao_aluno if observacao_aluno else "_Sem observação_"

        linha = discord.ui.ActionRow()
        if valor_total > 0:
            botao_moedas = discord.ui.Button(
                label=f"Pagar com moedas ({moedas})",
                style=discord.ButtonStyle.success,
                emoji="🪙",
            )
            botao_moedas.callback = self._ao_pagar_moedas
            linha.add_item(botao_moedas)

            botao_ingame = discord.ui.Button(
                label="Pagar in-game",
                style=discord.ButtonStyle.primary,
                emoji="💵",
            )
            botao_ingame.callback = self._ao_pagar_ingame
            linha.add_item(botao_ingame)
        else:
            botao_gratis = discord.ui.Button(
                label="Registrar solicitação",
                style=discord.ButtonStyle.success,
                emoji="📋",
            )
            botao_gratis.callback = self._ao_gratuito
            linha.add_item(botao_gratis)

        botao_cancelar = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
        )
        botao_cancelar.callback = self._ao_cancelar
        linha.add_item(botao_cancelar)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# Confirmar pedido de curso\n"
                    f"{lista}\n\n"
                    f"**Total in-game:** {formatar_reais(valor_total)}\n"
                    + (
                        f"**Moedas necessárias:** `{moedas}` "
                        f"(1 moeda = {formatar_reais(VALOR_MOEDA_INGAME)})\n"
                        if valor_total > 0
                        else ""
                    )
                    + f"**Observação:** {obs_txt}"
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

    async def _ao_pagar_moedas(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await finalizar_pedido(
            interacao,
            chaves=self.chaves,
            forma="MOEDAS",
            observacao_aluno=self.observacao_aluno,
        )

    async def _ao_pagar_ingame(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await finalizar_pedido(
            interacao,
            chaves=self.chaves,
            forma="IN_GAME",
            observacao_aluno=self.observacao_aluno,
        )

    async def _ao_gratuito(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await finalizar_pedido(
            interacao,
            chaves=self.chaves,
            forma="GRATUITO",
            observacao_aluno=self.observacao_aluno,
        )

    async def _ao_cancelar(self, interacao: discord.Interaction):
        await responder_aviso(
            interacao,
            titulo="Cancelado",
            linhas=["Pedido de curso cancelado."],
            delay=8,
        )


async def finalizar_pedido(
    interacao: discord.Interaction,
    *,
    chaves: list[str],
    forma: str,
    observacao_aluno: str,
) -> None:
    membro = interacao.user
    if not isinstance(membro, discord.Member):
        await responder_erro(
            interacao,
            titulo="Apenas no servidor",
            linhas=["Use o painel no Discord do hospital."],
        )
        return

    try:
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
                    + ", ".join(rotulo_curso(c) for c in chaves_novas),
                    "Não é criado um segundo card.",
                ],
                delay=18,
            )
            return

        moedas = 0
        saldo_restante = None
        if forma == "MOEDAS":
            moedas = moedas_necessarias_para_pacote(chaves_para_adicionar)
            ok, saldo_restante, erro_txt = await debitar_moedas_curso(membro.id, moedas)
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
                + ", ".join(rotulo_curso(c) for c in chaves_para_adicionar),
                f"Forma: `{forma}`"
                + (
                    f" · Moedas debitadas agora: `{moedas}` · Saldo: `{saldo_restante}`"
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
            )
            ok_post = await publicar_no_agendamentos(
                interacao.guild,
                membro=membro,
                registro=registro,
            )
            titulo_ok = "Pedido enviado ao agendamento"
            linhas_ok = [
                f"Pedido `#{registro.id}` publicado.",
                f"Forma: `{forma}`"
                + (
                    f" · Moedas: `{moedas}` · Saldo: `{saldo_restante}`"
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
                    f"Pedido `#{registro.id}` gravado, mas o card de agendamento falhou.",
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
    """Mensagem em CANAL_AGENDAMENTOS — botão único Aceitar."""

    def __init__(
        self,
        *,
        titulo: str,
        corpo: str,
        guild: discord.Guild,
        solicitacao_id: int,
        url_avatar: str | None,
        ja_aceito: bool = False,
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

        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="Aceitar Solicitação" if not ja_aceito else "Aceito ✓",
            style=(
                discord.ButtonStyle.success
                if not ja_aceito
                else discord.ButtonStyle.secondary
            ),
            emoji="✅",
            custom_id=f"{CUSTOM_ID_ACEITAR}{solicitacao_id}",
            disabled=ja_aceito,
        )
        botao.callback = self._ao_aceitar
        linha.add_item(botao)
        componentes.append(linha)
        componentes.append(discord.ui.TextDisplay(_rodape(guild)))

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=(
                    discord.Color.green() if ja_aceito else discord.Color.dark_gold()
                ),
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
            titulo, corpo = montar_linhas_corpo_pedido(
                membro=aluno or interacao.user,  # type: ignore[arg-type]
                registro=registro,
            )
            if obs:
                corpo += f"\n\n### 📌 Observação do instrutor\n> {obs}"
            corpo += f"\n**Instrutor:** {interacao.user.mention}"

            # Desativa botão no agendamento
            if self.mensagem_agendamento is not None and guilda is not None:
                url_avatar = (
                    aluno.display_avatar.url
                    if aluno is not None
                    else interacao.user.display_avatar.url
                )
                try:
                    await self.mensagem_agendamento.edit(
                        view=ViewAceitarAgendamento(
                            titulo=titulo,
                            corpo=corpo + "\n\n-# ✅ **Solicitação aceita**",
                            guild=guilda,
                            solicitacao_id=registro.id,
                            url_avatar=url_avatar,
                            ja_aceito=True,
                        )
                    )
                except discord.HTTPException as erro:
                    await enviar_erro_para_log_erros(
                        guilda,
                        "Falha ao desativar botão de agendamento",
                        erro,
                        contexto="ModalObservacaoInstrutor.edit",
                        usuario=interacao.user,
                    )

            # Publica em aprovar/reprovar
            await publicar_para_decisao(guilda, registro=registro, aluno=aluno)

            # DM do aluno
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
    ):
        """
        modo: normal | selecionar_aprovar | selecionar_reprovar | final
        """
        super().__init__(timeout=None)
        self.solicitacao_id = solicitacao_id
        self.titulo = titulo
        self.corpo = corpo
        self.guild_ref = guild
        self.url_avatar = url_avatar
        self.chaves_cursos = list(chaves_cursos or [])

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
            seletor.callback = (
                self._ao_select_aprovar
                if modo == "selecionar_aprovar"
                else self._ao_select_reprovar
            )
            linha.add_item(seletor)
            componentes.append(linha)
            linha2 = discord.ui.ActionRow()
            botao_cancelar = discord.ui.Button(
                label="Cancelar",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{CUSTOM_ID_CANCELA_DECISAO}{solicitacao_id}",
            )
            botao_cancelar.callback = self._ao_cancelar_confirmacao
            linha2.add_item(botao_cancelar)
            componentes.append(linha2)
        else:
            botao_ok = discord.ui.Button(
                label="Aprovar",
                style=discord.ButtonStyle.success,
                emoji="✅",
                custom_id=f"{CUSTOM_ID_APROVAR}{solicitacao_id}",
            )
            botao_ok.callback = self._ao_abrir_select_aprovar
            botao_nao = discord.ui.Button(
                label="Reprovar",
                style=discord.ButtonStyle.danger,
                emoji="❌",
                custom_id=f"{CUSTOM_ID_REPROVAR}{solicitacao_id}",
            )
            botao_nao.callback = self._ao_abrir_select_reprovar
            linha.add_item(botao_ok)
            linha.add_item(botao_nao)
            componentes.append(linha)

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
        await interacao.response.edit_message(
            view=ViewDecisaoCurso(
                titulo=self.titulo,
                corpo=self.corpo,
                guild=self.guild_ref,
                solicitacao_id=self.solicitacao_id,
                url_avatar=self.url_avatar,
                modo="selecionar_aprovar",
                chaves_cursos=chaves,
            )
        )

    async def _ao_abrir_select_reprovar(self, interacao: discord.Interaction):
        if not await self._checar_perm(interacao):
            return
        chaves = self.chaves_cursos or await self._carregar_chaves()
        await interacao.response.edit_message(
            view=ViewDecisaoCurso(
                titulo=self.titulo,
                corpo=self.corpo,
                guild=self.guild_ref,
                solicitacao_id=self.solicitacao_id,
                url_avatar=self.url_avatar,
                modo="selecionar_reprovar",
                chaves_cursos=chaves,
            )
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
        await interacao.response.edit_message(
            view=ViewDecisaoCurso(
                titulo=self.titulo,
                corpo=self.corpo,
                guild=self.guild_ref,
                solicitacao_id=self.solicitacao_id,
                url_avatar=self.url_avatar,
                modo="normal",
                chaves_cursos=chaves,
            )
        )

    async def _ao_select_aprovar(self, interacao: discord.Interaction):
        if not await self._checar_perm(interacao):
            return
        marcados = list((interacao.data or {}).get("values") or [])
        todas = self.chaves_cursos or await self._carregar_chaves()
        aprovadas = [c for c in todas if c in marcados]
        reprovadas = [c for c in todas if c not in marcados]
        await self._aplicar_decisao_parcial(interacao, aprovadas, reprovadas)

    async def _ao_select_reprovar(self, interacao: discord.Interaction):
        if not await self._checar_perm(interacao):
            return
        marcados = list((interacao.data or {}).get("values") or [])
        todas = self.chaves_cursos or await self._carregar_chaves()
        reprovadas = [c for c in todas if c in marcados]
        aprovadas = [c for c in todas if c not in marcados]
        await self._aplicar_decisao_parcial(interacao, aprovadas, reprovadas)

    async def _aplicar_decisao_parcial(
        self,
        interacao: discord.Interaction,
        aprovadas: list[str],
        reprovadas: list[str],
    ):
        membro = interacao.user
        assert isinstance(membro, discord.Member)
        try:
            await interacao.response.defer(ephemeral=True)
            registro = await decidir_cursos_parciais(
                solicitacao_id=self.solicitacao_id,
                chaves_aprovadas=aprovadas,
                chaves_reprovadas=reprovadas,
                instrutor_id=membro.id,
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
                if registro.forma_pagamento == "MOEDAS" and registro.moedas_debitadas:
                    # Credita só a fatia dos cursos aprovados
                    valor_aprov = soma_valor_ingame(aprovadas)
                    moedas_credito = (
                        moedas_necessarias_para_pacote(aprovadas) if valor_aprov else 0
                    )
                    if moedas_credito:
                        await creditar_moedas_instrutor(membro.id, moedas_credito)

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

            resumo = (
                f"Aprovados: {', '.join(rotulo_curso(c) for c in aprovadas) or '—'}\n"
                f"Reprovados: {', '.join(rotulo_curso(c) for c in reprovadas) or '—'}"
            )
            try:
                await interacao.message.edit(
                    view=ViewDecisaoCurso(
                        titulo=self.titulo,
                        corpo=self.corpo + f"\n\n-# **Decisão:**\n{resumo}",
                        guild=guilda,
                        solicitacao_id=registro.id,
                        url_avatar=self.url_avatar,
                        modo="final",
                        desabilitada=True,
                        chaves_cursos=self.chaves_cursos,
                    )
                )
            except discord.HTTPException:
                pass

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


async def publicar_no_agendamentos(
    guilda: discord.Guild | None,
    *,
    membro: discord.Member,
    registro,
) -> bool:
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
    """Edita o card existente do pedido; se não achar a mensagem, publica de novo."""
    if guilda is None:
        return False
    titulo, corpo = montar_linhas_corpo_pedido(membro=membro, registro=registro)
    view = ViewAceitarAgendamento(
        titulo=titulo,
        corpo=corpo,
        guild=guilda,
        solicitacao_id=registro.id,
        url_avatar=membro.display_avatar.url,
        ja_aceito=registro.status == "ACEITO",
    )

    if registro.mensagem_id and registro.mensagem_canal_id:
        canal = guilda.get_channel(int(registro.mensagem_canal_id))
        if canal is not None:
            try:
                mensagem = await canal.fetch_message(int(registro.mensagem_id))
                await mensagem.edit(view=view)
                return True
            except (discord.NotFound, discord.HTTPException) as erro:
                await enviar_erro_para_log_erros(
                    guilda,
                    "Falha ao editar card de agendamento — republicando",
                    erro,
                    contexto="atualizar_ou_publicar_agendamento.edit",
                    usuario=membro,
                )

    return await publicar_no_agendamentos(guilda, membro=membro, registro=registro)


async def publicar_para_decisao(
    guilda: discord.Guild | None,
    *,
    registro,
    aluno: discord.Member | None,
) -> None:
    if guilda is None:
        return
    canal_id = CANAIS.get("CANAL_APROVAR_REPROVAR_CURSO")
    canal = guilda.get_channel(int(canal_id)) if canal_id else None
    if canal is None:
        await enviar_erro_para_log_erros(
            guilda,
            "CANAL_APROVAR_REPROVAR_CURSO não encontrado",
            RuntimeError(f"id={canal_id}"),
            contexto="publicar_para_decisao",
        )
        return

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
    if registro.observacao_instrutor:
        corpo += (
            f"\n\n### 📌 Observação do instrutor\n> {registro.observacao_instrutor}"
        )
    url = getattr(getattr(membro_ref, "display_avatar", None), "url", None)
    chaves = parse_chaves_json(registro.chaves_cursos_json, registro.chave_curso)
    try:
        await canal.send(
            view=ViewDecisaoCurso(
                titulo=titulo,
                corpo=corpo,
                guild=guilda,
                solicitacao_id=registro.id,
                url_avatar=url,
                modo="normal",
                chaves_cursos=chaves,
            )
        )
    except discord.HTTPException as erro:
        await enviar_erro_para_log_erros(
            guilda,
            "Falha ao postar decisão de curso",
            erro,
            contexto="publicar_para_decisao.send",
        )


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
        bloco_cursos = f"**{'🌄 Curso aplicado' if aprovado else 'Curso'}:** {menção_cargo_curso(chave)}"
    else:
        lista = "\n".join(f"> {menção_cargo_curso(c)}" for c in chaves)
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


def view_persistente_cursos() -> PainelCursosLayout:
    return PainelCursosLayout()
