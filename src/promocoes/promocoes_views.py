"""Painel e botões de solicitação / aprovação de promoção."""

from __future__ import annotations

import discord

from src.config import CANAIS
from src.plantao.plantao_permissoes import e_diretoria
from src.promocoes.promocoes_service import (
    aplicar_promocao_cargos,
    atualizar_mensagem_solicitacao,
    cargo_mais_alto_do_membro,
    criar_solicitacao_promocao,
    decidir_solicitacao,
    listar_cargos_destino_para_membro,
    membro_e_paramedico,
    membro_ja_tem_area,
    montar_checklist_trilha_async,
    obter_solicitacao,
    obter_solicitacao_pendente,
    obter_trilha,
    obter_trilha_por_destino_e_origem,
    registrar_historico,
    trilhas_a_partir_do_membro,
    trilhas_para_cargo_destino,
)
from src.utils.error_handling import (
    LoggingViewMixin,
    enviar_erro_para_log_erros,
    ignorar_falha_cosmetica,
)
from src.utils.mensagens import (
    COR_ERRO,
    COR_SUCESSO,
    editar_mensagem_original,
    responder_aviso,
    responder_erro,
    responder_sucesso,
    responder_view,
)

CUSTOM_ID_BOTAO_SOLICITAR = "promocoes:botao_solicitar"
CUSTOM_ID_SELECT_CARGO = "promocoes:select_cargo"
CUSTOM_ID_BOTAO_TRILHA = "promocoes:botao_trilha"
CUSTOM_ID_SELECT_TRILHA = "promocoes:select_trilha"
CUSTOM_ID_APROVAR = "promocoes:aprovar:"
CUSTOM_ID_REPROVAR = "promocoes:reprovar:"


class PainelPromocaoLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel persistente no padrão do recrutamento."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(timeout=None)

        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="Solicitar promoção",
            style=discord.ButtonStyle.success,
            emoji="⬆️",
            custom_id=CUSTOM_ID_BOTAO_SOLICITAR,
        )
        botao.callback = self._ao_solicitar
        linha.add_item(botao)

        url_icone = None
        if guild is not None and guild.icon is not None:
            url_icone = guild.icon.url

        texto = (
            "Peça avanço de cargo conforme a hierarquia do hospital.\n\n"
            "O bot confere **advertências**, **cursos**, **plantão** e **metas** "
            "(laudos, chamadas, recrutamentos) conforme o modo escolhido."
        )
        if url_icone:
            bloco_topo = discord.ui.Section(
                "# ⬆️ Painel de Promoções",
                texto,
                accessory=discord.ui.Thumbnail(url_icone),
            )
        else:
            bloco_topo = discord.ui.TextDisplay("# ⬆️ Painel de Promoções\n" + texto)

        self.add_item(
            discord.ui.Container(
                bloco_topo,
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(
                    "## 📌 Antes de solicitar\n\n"
                    "✅ Sem **Adv 01** / **Adv 02** ativa.\n"
                    "✅ **Seguir trilha** — exige cargo de origem, cursos, plantão e "
                    "**metas** (chamadas, laudos, etc.).\n"
                    "✅ **Cargo pretendido** — para **Paramédico** pedindo primeira "
                    "área (ex.: Psicólogo): só **cursos obrigatórios**, sem metas.\n"
                    "✅ Demais cargos no menu seguem a trilha completa."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _ao_solicitar(self, interacao: discord.Interaction):
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Apenas no servidor",
                linhas=["Use o painel dentro do Discord do hospital."],
            )
            return
        try:
            await responder_view(
                interacao,
                ViewEscolhaPromocao(membro),
                ephemeral=True,
            )
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro ao abrir solicitação de promoção",
                erro,
                contexto="PainelPromocaoLayout._ao_solicitar",
                usuario=membro,
            )
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=["Não foi possível abrir a solicitação."],
            )


class ViewEscolhaPromocao(LoggingViewMixin, discord.ui.LayoutView):
    """Select de cargos-destino + botão Seguir trilha (opcional)."""

    def __init__(self, membro: discord.Member):
        super().__init__(timeout=180)
        self.solicitante_id = membro.id

        cargo_atual = cargo_mais_alto_do_membro(membro)
        destinos = listar_cargos_destino_para_membro(membro)
        opcoes = [
            discord.SelectOption(
                label=nome[:100],
                value=nome,
                description="Disponível a partir do seu cargo",
            )
            for nome in destinos[:25]
        ]
        if not opcoes:
            opcoes = [
                discord.SelectOption(
                    label="Nenhuma promoção disponível",
                    value="_vazio",
                )
            ]

        linha_select = discord.ui.ActionRow()
        seletor = discord.ui.Select(
            placeholder="Selecione o cargo pretendido…",
            options=opcoes,
            min_values=1,
            max_values=1,
            custom_id=CUSTOM_ID_SELECT_CARGO,
        )
        seletor.callback = self._ao_escolher_cargo
        linha_select.add_item(seletor)

        linha_botao = discord.ui.ActionRow()
        botao_trilha = discord.ui.Button(
            label="Seguir trilha",
            style=discord.ButtonStyle.primary,
            emoji="🛤️",
            custom_id=CUSTOM_ID_BOTAO_TRILHA,
        )
        botao_trilha.callback = self._ao_seguir_trilha
        linha_botao.add_item(botao_trilha)

        texto_cargo = (
            f"Cargo mais alto reconhecido: **`{cargo_atual}`**\n"
            if cargo_atual
            else "Não encontrei um cargo da hierarquia em você.\n"
        )
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# Solicitar promoção\n"
                    f"{texto_cargo}"
                    "Só aparecem cargos **a partir do seu posto atual** "
                    "(nada do que você já passou).\n"
                    "Escolha o **cargo pretendido** ou use **Seguir trilha**."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha_select,
                linha_botao,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _ao_escolher_cargo(self, interacao: discord.Interaction):
        if interacao.user.id != self.solicitante_id:
            await responder_erro(
                interacao,
                titulo="Não é sua solicitação",
                linhas=["Só quem abriu o painel pode continuar."],
            )
            return
        valores = interacao.data.get("values") if interacao.data else None
        if not valores or valores[0] == "_vazio":
            await responder_erro(
                interacao,
                titulo="Seleção inválida",
                linhas=["Escolha um cargo pretendido."],
            )
            return
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Apenas no servidor",
                linhas=["Use o painel no servidor."],
            )
            return
        cargo_destino = valores[0]
        # Paramédico SEM área ainda = primeira área (cursos + horas da área).
        # Quem já tem área ou outro cargo = checklist completo com metas.
        if membro_e_paramedico(membro) and not membro_ja_tem_area(membro):
            candidatas = trilhas_para_cargo_destino(cargo_destino)
            trilha = candidatas[0] if candidatas else None
            modo = "primeira_area_paramedico"
        else:
            trilha = obter_trilha_por_destino_e_origem(cargo_destino, membro)
            modo = "trilha"
        if trilha is None:
            await responder_erro(
                interacao,
                titulo="Trilha não encontrada",
                linhas=[
                    f"Não há promoção cadastrada para `{cargo_destino}`.",
                    "Fale com a diretoria ou use **Seguir trilha**.",
                ],
            )
            return
        await processar_escolha_trilha(interacao, trilha["chave"], modo=modo)

    async def _ao_seguir_trilha(self, interacao: discord.Interaction):
        if interacao.user.id != self.solicitante_id:
            await responder_erro(
                interacao,
                titulo="Não é sua solicitação",
                linhas=["Só quem abriu o painel pode continuar."],
            )
            return
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Apenas no servidor",
                linhas=["Use o painel no servidor."],
            )
            return
        disponiveis = trilhas_a_partir_do_membro(membro)
        cargo_atual = cargo_mais_alto_do_membro(membro)
        if not disponiveis:
            await responder_aviso(
                interacao,
                titulo="Nenhuma trilha a partir do seu cargo",
                linhas=[
                    (
                        f"Cargo reconhecido: `{cargo_atual}`."
                        if cargo_atual
                        else "Nenhum cargo da hierarquia encontrado."
                    ),
                    "Não há promoção cadastrada partindo desse posto.",
                    "Se achar que falta alguma trilha, fale com a diretoria.",
                ],
                delay=60,
            )
            return
        await editar_mensagem_original(
            interacao,
            view=ViewSelectTrilha(membro, disponiveis),
        )


class ViewSelectTrilha(LoggingViewMixin, discord.ui.LayoutView):
    """Lista só as trilhas a partir do cargo atual do membro."""

    def __init__(self, membro: discord.Member, trilhas: list[dict]):
        super().__init__(timeout=180)
        self.solicitante_id = membro.id

        opcoes = [
            discord.SelectOption(
                label=trilha["rotulo"][:100],
                value=trilha["chave"],
                description=(trilha.get("observacao") or "")[:100],
            )
            for trilha in trilhas[:25]
        ]
        linha = discord.ui.ActionRow()
        seletor = discord.ui.Select(
            placeholder="Trilha a partir do seu cargo…",
            options=opcoes,
            min_values=1,
            max_values=1,
            custom_id=CUSTOM_ID_SELECT_TRILHA,
        )
        seletor.callback = self._ao_escolher
        linha.add_item(seletor)

        cargo_atual = cargo_mais_alto_do_membro(membro)
        lista_de_trilhas = "\n".join(
            f"• **{trilha_disponivel['rotulo']}**" for trilha_disponivel in trilhas
        )
        cabecalho = f"Cargo atual: **`{cargo_atual}`**\n" if cargo_atual else ""
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# 🛤️ Seguir trilha\n"
                    f"{cabecalho}"
                    "Só as opções a partir do **seu posto atual**:\n"
                    f"{lista_de_trilhas}"
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _ao_escolher(self, interacao: discord.Interaction):
        if interacao.user.id != self.solicitante_id:
            await responder_erro(
                interacao,
                titulo="Não é sua solicitação",
                linhas=["Só quem abriu o painel pode continuar."],
            )
            return
        valores = interacao.data.get("values") if interacao.data else None
        if not valores:
            await responder_erro(
                interacao,
                titulo="Seleção inválida",
                linhas=["Escolha uma trilha."],
            )
            return
        await processar_escolha_trilha(interacao, valores[0], modo="trilha")


class ViewDecisaoPromocao(LoggingViewMixin, discord.ui.LayoutView):
    """Botões Aprovar / Reprovar no canal da diretoria."""

    def __init__(self, solicitacao_id: int, *, desabilitada: bool = False):
        super().__init__(timeout=None)
        self.solicitacao_id = solicitacao_id

        linha = discord.ui.ActionRow()
        botao_ok = discord.ui.Button(
            label="Aprovar",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"{CUSTOM_ID_APROVAR}{solicitacao_id}",
            disabled=desabilitada,
        )
        botao_ok.callback = self._ao_aprovar
        botao_nao = discord.ui.Button(
            label="Reprovar",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"{CUSTOM_ID_REPROVAR}{solicitacao_id}",
            disabled=desabilitada,
        )
        botao_nao.callback = self._ao_reprovar
        linha.add_item(botao_ok)
        linha.add_item(botao_nao)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("-# Ação da diretoria"),
                linha,
                accent_color=discord.Color.dark_gold(),
            )
        )

    async def _ao_aprovar(self, interacao: discord.Interaction):
        await self._decidir(interacao, aprovada=True)

    async def _ao_reprovar(self, interacao: discord.Interaction):
        await self._decidir(interacao, aprovada=False)

    async def _decidir(self, interacao: discord.Interaction, *, aprovada: bool):
        membro = interacao.user
        if not isinstance(membro, discord.Member) or not e_diretoria(membro):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas **Diretoria+** pode decidir promoções."],
            )
            return

        try:
            await interacao.response.defer(ephemeral=True)
            registro = await obter_solicitacao(self.solicitacao_id)
            if registro is None:
                await responder_erro(
                    interacao,
                    titulo="Pedido não encontrado",
                    linhas=[f"ID `{self.solicitacao_id}` não existe."],
                )
                return
            if registro.status != "PENDENTE":
                await responder_aviso(
                    interacao,
                    titulo="Pedido já decidido",
                    linhas=[
                        f"Solicitação `#{registro.id}` já está como `{registro.status}"
                        f"`.",
                        "Não é possível aprovar ou reprovar de novo.",
                    ],
                    delay=12,
                )
                try:
                    if interacao.message is not None:
                        await interacao.message.delete()
                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException,
                ) as erro_em_decidir:
                    # Enfeite que falhou: atualizar o card da decisao.
                    # A acao principal ja tinha dado certo, entao so registro.
                    ignorar_falha_cosmetica(
                        erro_em_decidir,
                        o_que_falhou="atualizar o card da decisao",
                    )
                return

            registro, foi_decidido_agora = await decidir_solicitacao(
                solicitacao_id=self.solicitacao_id,
                aprovada=aprovada,
                analisado_por=membro.id,
                motivo=None if aprovada else "Reprovado pela diretoria",
            )
            if registro is None:
                await responder_erro(
                    interacao,
                    titulo="Falha ao atualizar pedido",
                    linhas=["Não foi possível atualizar o pedido."],
                )
                return
            # Outro staff clicou primeiro — não posta card de novo
            if not foi_decidido_agora:
                await responder_aviso(
                    interacao,
                    titulo="Pedido já decidido",
                    linhas=[
                        f"Solicitação `#{registro.id}` já está como `{registro.status}"
                        f"`.",
                        "Outra pessoa da diretoria já registrou a decisão.",
                    ],
                    delay=12,
                )
                try:
                    if interacao.message is not None:
                        await interacao.message.delete()
                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException,
                ) as erro_em_decidir:
                    # Enfeite que falhou: atualizar o card da decisao.
                    # A acao principal ja tinha dado certo, entao so registro.
                    ignorar_falha_cosmetica(
                        erro_em_decidir,
                        o_que_falhou="atualizar o card da decisao",
                    )
                return

            guilda = interacao.guild
            alvo = guilda.get_member(registro.discord_id) if guilda else None

            if aprovada and alvo is not None:
                ok, detalhe = await aplicar_promocao_cargos(
                    alvo,
                    registro.cargo_de,
                    registro.cargo_para,
                    executor=membro,
                )
                if not ok:
                    await enviar_erro_para_log_erros(
                        guilda,
                        "Promoção aprovada mas falha ao dar cargo",
                        RuntimeError(detalhe),
                        contexto="ViewDecisaoPromocao.aplicar_promocao",
                        usuario=membro,
                    )
                    await responder_erro(
                        interacao,
                        titulo="Aprovado no sistema, falha nos cargos",
                        linhas=[detalhe, "Ajuste os cargos manualmente se preciso."],
                    )
                await registrar_historico(
                    discord_id=registro.discord_id,
                    tipo="PROMOCAO",
                    cargo_de=registro.cargo_de,
                    cargo_para=registro.cargo_para,
                    motivo="Aprovado pela diretoria",
                    executado_por=membro.id,
                    solicitacao_id=registro.id,
                )
                await _postar_resultado_publico(
                    guilda,
                    aprovada=True,
                    alvo_id=registro.discord_id,
                    cargo_de=registro.cargo_de,
                    cargo_para=registro.cargo_para,
                    staff=membro,
                    solicitacao_id=registro.id,
                )
            else:
                await registrar_historico(
                    discord_id=registro.discord_id,
                    tipo="NAO_PROMOVIDO",
                    cargo_de=registro.cargo_de,
                    cargo_para=registro.cargo_para,
                    motivo="Reprovado pela diretoria",
                    executado_por=membro.id,
                    solicitacao_id=registro.id,
                )
                await _postar_resultado_publico(
                    guilda,
                    aprovada=False,
                    alvo_id=registro.discord_id,
                    cargo_de=registro.cargo_de,
                    cargo_para=registro.cargo_para,
                    staff=membro,
                    solicitacao_id=registro.id,
                )

            # DM + log LOG_NOTIFICACOES_DM
            from src.utils.notificacao import notificar_dm_promocao_resultado

            await notificar_dm_promocao_resultado(
                alvo=alvo,
                aprovada=aprovada,
                cargo_de=registro.cargo_de,
                cargo_para=registro.cargo_para,
                solicitacao_id=registro.id,
                staff=membro,
                guilda=guilda,
            )

            # Após a decisão o card some do canal de aprovar/recusar —
            # o resultado público já foi para promovidos / não promovidos.
            try:
                if interacao.message is not None:
                    await interacao.message.delete()
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ) as erro_em_decidir:
                # Enfeite que falhou: atualizar o card da decisao.
                # A acao principal ja tinha dado certo, entao so registro.
                ignorar_falha_cosmetica(
                    erro_em_decidir,
                    o_que_falhou="atualizar o card da decisao",
                )

            await responder_sucesso(
                interacao,
                titulo="Promoção aprovada" if aprovada else "Promoção reprovada",
                linhas=[f"Pedido `#{registro.id}` finalizado."],
                delay=12,
            )
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro ao decidir promoção",
                erro,
                contexto="ViewDecisaoPromocao._decidir",
                usuario=membro,
            )
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=["Falha na decisão. Veja LOG_ERROS."],
            )


async def processar_escolha_trilha(
    interacao: discord.Interaction,
    chave_trilha: str,
    *,
    modo: str = "trilha",
) -> None:
    """
    Atende o clique do membro numa trilha de carreira.

    ``modo``:
    - ``trilha``: cargo origem + cursos + plantão + metas (Seguir trilha).
    - ``primeira_area_paramedico``: cursos + plantão da área, sem metas
      de produção (Paramédico ainda sem área).
    """
    membro = interacao.user
    if not isinstance(membro, discord.Member):
        await responder_erro(
            interacao,
            titulo="Apenas no servidor",
            linhas=["Use o painel no Discord do hospital."],
        )
        return

    trilha = obter_trilha(chave_trilha)
    if trilha is None:
        await responder_erro(
            interacao,
            titulo="Trilha inválida",
            linhas=[f"Chave `{chave_trilha}` não cadastrada."],
        )
        return

    if modo == "primeira_area_paramedico" and not membro_e_paramedico(membro):
        modo = "trilha"

    try:
        if not interacao.response.is_done():
            await interacao.response.defer(ephemeral=True)

        checklist = await montar_checklist_trilha_async(membro, trilha, modo=modo)
        if not checklist.get("pode_enviar"):
            await responder_aviso(
                interacao,
                titulo=checklist.get("titulo_card") or "Requisitos incompletos",
                linhas=list(
                    checklist.get("linhas")
                    or ["Não foi possível enviar a solicitação."]
                ),
                delay=60,
                com_marcador=False,
            )
            return

        # Bloqueia segundo pedido enquanto houver um ainda PENDENTE
        pedido_aberto = await obter_solicitacao_pendente(membro.id)
        if pedido_aberto is not None:
            await responder_aviso(
                interacao,
                titulo="Solicitação já enviada",
                linhas=[
                    f"Você já tem o pedido `#{pedido_aberto.id}` aguardando a "
                    f"diretoria.",
                    f"**Trilha:** `{pedido_aberto.chave_trilha}`",
                    f"**De:** `{pedido_aberto.cargo_de}` → **Para:** "
                    f"`{pedido_aberto.cargo_para}"
                    f"`",
                    "Espere a análise (aprovar / reprovar) antes de enviar outro.",
                ],
                delay=20,
            )
            return

        # Em primeira área, registra origem real (Paramédico), não o de_cargo da trilha
        cargo_origem_registro = (
            "🚑・Paramédico"
            if modo == "primeira_area_paramedico"
            else trilha["de_cargo"]
        )
        registro = await criar_solicitacao_promocao(
            discord_id=membro.id,
            chave_trilha=chave_trilha
            + (":primeira_area" if modo == "primeira_area_paramedico" else ""),
            cargo_de=cargo_origem_registro,
            cargo_para=trilha["para_cargo"],
            resumo_checklist="\n".join(checklist.get("linhas") or []),
        )

        guilda = interacao.guild
        canal_dest_id = CANAIS.get("CANAL_APROVAR_RECUSAR_PROMO") or CANAIS.get(
            "CANAL_PROMOVIDOS"
        )
        canal = (
            guilda.get_channel(int(canal_dest_id)) if guilda and canal_dest_id else None
        )

        resumo = "\n".join(checklist.get("linhas") or [])
        corpo = (
            f"> - **👤 Membro:** {membro.mention} (`{membro.id}`)\n"
            f"> - **📋 Solicitação:** `#{registro.id}`\n"
            f"> - **🛤️ Trilha:** {checklist.get('rotulo', trilha['rotulo'])}\n"
            f"> - **🎯 De:** `{trilha['de_cargo']}` → **Para:** "
            f"`{trilha['para_cargo']}`\n\n"
            f"**Checklist:**\n{resumo}"
        )

        if canal is not None:
            try:
                view_pedido = _ViewPedidoPromocao(
                    titulo=f"📋 Solicitação de promoção · #{registro.id}",
                    corpo=corpo,
                    guild=guilda,
                    solicitacao_id=registro.id,
                    url_avatar=membro.display_avatar.url,
                )
                # Garante que Aprovar/Reprovar sobrevivem a reinício do bot
                interacao.client.add_view(view_decisao_persistente(registro.id))
                mensagem = await canal.send(view=view_pedido)
                await atualizar_mensagem_solicitacao(registro.id, canal.id, mensagem.id)
            except discord.HTTPException as erro:
                await enviar_erro_para_log_erros(
                    guilda,
                    "Falha ao postar pedido de promoção",
                    erro,
                    contexto="processar_escolha_trilha.send",
                    usuario=membro,
                )
                await responder_erro(
                    interacao,
                    titulo="Falha ao enviar à diretoria",
                    linhas=[
                        "Pedido salvo no banco, mas não postou no canal. Veja "
                        "LOG_ERROS."
                    ],
                )
                return
        else:
            await enviar_erro_para_log_erros(
                guilda,
                "Canal de promoções não configurado",
                RuntimeError("CANAL_APROVAR_RECUSAR_PROMO ausente"),
                contexto="processar_escolha_trilha",
                usuario=membro,
            )

        await responder_sucesso(
            interacao,
            titulo="Solicitação enviada",
            linhas=[
                f"Pedido `#{registro.id}` · **{trilha['rotulo']}**",
                f"**De:** `{trilha['de_cargo']}` → **Para:** `{trilha['para_cargo']}`",
                "A diretoria vai analisar **Aprovar / Reprovar** no canal de "
                "promoções.",
                "",
                "### Requisitos no momento do envio",
                *(checklist.get("linhas") or []),
            ],
            delay=60,
            com_marcador=False,
        )
    except Exception as erro:
        await enviar_erro_para_log_erros(
            interacao.guild,
            "Erro ao processar promoção",
            erro,
            contexto="processar_escolha_trilha",
            usuario=membro,
        )
        await responder_erro(
            interacao,
            titulo="Erro inesperado",
            linhas=["Falha na solicitação. A equipe foi notificada."],
        )


class _ViewPedidoPromocao(LoggingViewMixin, discord.ui.LayoutView):
    """Card no canal de aprovar/recusar — padrão Components V2."""

    def __init__(
        self,
        *,
        titulo: str,
        corpo: str,
        guild: discord.Guild,
        solicitacao_id: int,
        url_avatar: str | None = None,
    ):
        super().__init__(timeout=None)

        componentes: list = [discord.ui.TextDisplay(f"# {titulo}")]
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
        botao_ok = discord.ui.Button(
            label="Aprovar",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"{CUSTOM_ID_APROVAR}{solicitacao_id}",
        )
        botao_nao = discord.ui.Button(
            label="Reprovar",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"{CUSTOM_ID_REPROVAR}{solicitacao_id}",
        )
        botao_ok.callback = _bind_decidir(solicitacao_id, True)
        botao_nao.callback = _bind_decidir(solicitacao_id, False)
        linha.add_item(botao_ok)
        linha.add_item(botao_nao)
        componentes.append(linha)

        momento = int(
            __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .timestamp()
        )
        nome = guild.name if guild else "CENTRO MÉDICO SUL VALLEY"
        componentes.append(discord.ui.TextDisplay(f"-# 🏥 {nome} • <t:{momento}:f>"))

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.gold(),
            )
        )


def _bind_decidir(solicitacao_id: int, aprovada: bool):
    async def _ao_clicar(interacao: discord.Interaction):
        view = ViewDecisaoPromocao(solicitacao_id)
        await view._decidir(interacao, aprovada=aprovada)

    return _ao_clicar


async def _postar_resultado_publico(
    guilda: discord.Guild | None,
    *,
    aprovada: bool,
    alvo_id: int,
    cargo_de: str,
    cargo_para: str,
    staff: discord.Member,
    solicitacao_id: int,
) -> None:
    """
    Publica o resultado em um único canal:
    - aprovada → CANAL_PROMOVIDOS (fallback LOG_PROMOVIDOS)
    - reprovada → CANAL_NAO_PROMOVIDOS (fallback LOG_PROMOVIDOS)

    Antes o bot postava nos dois canais ao mesmo tempo e gerava card duplicado.
    """
    if guilda is None:
        return

    if aprovada:
        canal_id = CANAIS.get("CANAL_PROMOVIDOS") or CANAIS.get("LOG_PROMOVIDOS")
    else:
        canal_id = CANAIS.get("CANAL_NAO_PROMOVIDOS") or CANAIS.get("LOG_PROMOVIDOS")

    if not canal_id:
        await enviar_erro_para_log_erros(
            guilda,
            "Canal de resultado de promoção não configurado",
            RuntimeError("CANAL_PROMOVIDOS / CANAL_NAO_PROMOVIDOS ausentes"),
            contexto="_postar_resultado_publico",
            usuario=staff,
        )
        return

    canal = guilda.get_channel(int(canal_id))
    if canal is None:
        await enviar_erro_para_log_erros(
            guilda,
            "Canal de resultado de promoção não encontrado",
            RuntimeError(f"canal_id={canal_id}"),
            contexto="_postar_resultado_publico",
            usuario=staff,
        )
        return

    alvo = guilda.get_member(alvo_id)
    url_avatar = alvo.display_avatar.url if alvo is not None else None
    mencao_alvo = alvo.mention if alvo is not None else f"<@{alvo_id}>"

    if aprovada:
        titulo = "🚨 Membro Promovido"
        cor = COR_SUCESSO
    else:
        titulo = "🚫 Promoção recusada"
        cor = COR_ERRO

    corpo = (
        f"> - **👤 Membro:** {mencao_alvo}\n"
        f"> - **📋 Solicitação:** `#{solicitacao_id}`\n"
        f"> - **🎯 De:** `{cargo_de}` → **Para:** `{cargo_para}`\n"
        f"> - **👮 Responsável pela {'aprovação' if aprovada else 'reprovação'}:** "
        f"{staff.mention}"
    )

    from datetime import (
        datetime,
        timezone,
    )

    momento = int(datetime.now(timezone.utc).timestamp())
    rodape = f"-# 🏥 {guilda.name} • <t:{momento}:f>"

    componentes: list = [
        discord.ui.TextDisplay(f"# {titulo}\n"),
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
    componentes.append(discord.ui.TextDisplay(rodape))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*componentes, accent_color=cor))
    try:
        await canal.send(view=view)
    except discord.HTTPException as erro:
        await enviar_erro_para_log_erros(
            guilda,
            "Falha ao postar resultado de promoção",
            erro,
            contexto="_postar_resultado_publico",
            usuario=staff,
        )


def view_persistente_promocao() -> PainelPromocaoLayout:
    """
    Cria o painel de promocao para o bot registrar ao ligar.

    Existe como funcao, e nao como valor pronto, porque o bot precisa de uma
    instancia nova a cada vez que registra a view persistente.
    """
    return PainelPromocaoLayout()


def view_decisao_persistente(solicitacao_id: int) -> ViewDecisaoPromocao:
    """
    View dos botões Aprovar/Reprovar de um pedido.

    O custom_id inclui o id da solicitação. Por isso cada pedido pendente
    precisa de um ``add_view`` próprio — no envio e de novo no startup.
    """
    return ViewDecisaoPromocao(int(solicitacao_id))


async def registrar_views_persistentes_promocao(bot: discord.Client) -> int:
    """
    Registra o painel fixo e todos os botões de pedidos ainda pendentes.

    Devolve quantos pedidos pendentes tiveram view registrada.
    """
    from src.promocoes.promocoes_service import listar_ids_solicitacoes_pendentes

    bot.add_view(view_persistente_promocao())

    ids_pendentes = await listar_ids_solicitacoes_pendentes()
    for solicitacao_id in ids_pendentes:
        bot.add_view(view_decisao_persistente(solicitacao_id))
    return len(ids_pendentes)
