# src/demissao/demissao_panel.py
"""Painel fixo + fluxos ephemeral e de decisão da demissão."""

from __future__ import annotations

from datetime import datetime

import discord

from src.config import CANAIS
from src.demissao.demissao_service import (
    aplicar_cargos_demissao,
    cargo_atual_hierarquia,
    criar_solicitacao,
    decidir_demissao,
    marcar_mensagem_pedido,
    membro_e_diretoria,
    membro_pode_solicitar_demissao,
    obter_pedido_pendente,
)
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
    enviar_erro_para_log_erros,
    ignorar_falha_cosmetica,
)
from src.utils.formatacao import (
    agora_brasilia,
    para_horario_brasilia,
)
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_sucesso,
    responder_view,
)
from src.utils.notificacao import (
    COR_AVISO,
    COR_INFO,
    enviar_dm_card,
)

CUSTOM_ID_SOLICITAR = "demissao:solicitar"
CUSTOM_ID_APROVAR = "demissao:aprovar:"
CUSTOM_ID_REPROVAR = "demissao:reprovar:"


def _formatar_momento_brasilia(data: datetime | None = None) -> str:
    local = para_horario_brasilia(data) if data else agora_brasilia()
    if local is None:
        local = agora_brasilia()
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
    return (
        f"{local.day} {meses[local.month - 1]} {local.year} — {local.strftime('%H:%M')}"
    )


class PainelDemissaoLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel persistente no canal CANAL_PAINEL_DEMISSAO."""

    def __init__(self, guilda: discord.Guild | None = None):
        super().__init__(timeout=None)
        self.guild_ref = guilda

        url_icone = None
        if guilda is not None and guilda.icon is not None:
            url_icone = guilda.icon.url

        componentes: list = []
        titulo = (
            "# 🏥 CMS Valley — Solicitar Demissão\n"
            "> **Gerencie seu desligamento da organização de forma rápida e formal.**"
        )
        if url_icone:
            componentes.append(
                discord.ui.Section(
                    titulo,
                    accessory=discord.ui.Thumbnail(url_icone),
                )
            )
        else:
            componentes.append(discord.ui.TextDisplay(titulo))

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(
            discord.ui.TextDisplay(
                "## Sistema de Demissão\n\n"
                "Utilize o botão abaixo para solicitar o seu desligamento do CMS "
                "Valley.\n"
                "**Lembre-se:** ao confirmar, seu pedido será enviado para análise da "
                "diretoria."
            )
        )

        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="Solicitar Demissão",
            emoji="📤",
            style=discord.ButtonStyle.danger,
            custom_id=CUSTOM_ID_SOLICITAR,
        )
        botao.callback = self._ao_solicitar
        linha.add_item(botao)
        componentes.append(linha)

        componentes.append(
            discord.ui.TextDisplay(
                "-# 📤 **Solicitar Demissão:** clique no botão, informe o motivo e "
                "confirme."
            )
        )

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.dark_red(),
            )
        )

    async def _ao_solicitar(self, interacao: discord.Interaction):
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use este painel dentro do servidor."],
            )
            return

        if not membro_pode_solicitar_demissao(membro):
            await responder_aviso(
                interacao,
                titulo="Sem cargo na hierarquia",
                linhas=[
                    "Só membros da **hierarquia** hospitalar podem solicitar demissão.",
                ],
            )
            return

        pendente = await obter_pedido_pendente(membro.id)
        if pendente is not None:
            await responder_aviso(
                interacao,
                titulo="Pedido já em análise",
                linhas=[
                    f"Você já tem a solicitação `#{pendente.id}` **pendente**.",
                    "Aguarde a decisão da diretoria antes de abrir outra.",
                ],
                delay=15,
            )
            return

        await interacao.response.send_modal(ModalMotivoDemissao())


class ModalMotivoDemissao(
    LoggingModalMixin, discord.ui.Modal, title="📤 Motivo da demissão"
):
    campo_motivo = discord.ui.TextInput(
        label="Por que você está saindo?",
        style=discord.TextStyle.paragraph,
        placeholder="Descreva o motivo do desligamento…",
        required=True,
        min_length=5,
        max_length=1000,
    )

    async def on_submit(self, interacao: discord.Interaction):
        """Monta uma prévia confirmável do motivo de desligamento informado.

        Valida o contexto e o tamanho mínimo do motivo antes de mostrar os
        dados do cargo atual. Ainda não grava o pedido: a confirmação posterior
        existe para evitar que um envio acidental vire solicitação oficial.
        """
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use dentro do servidor."],
            )
            return

        motivo = (self.campo_motivo.value or "").strip()
        if len(motivo) < 5:
            await responder_erro(
                interacao,
                titulo="Motivo curto demais",
                linhas=["Escreva pelo menos algumas palavras."],
            )
            return

        cargo = cargo_atual_hierarquia(membro)
        momento = _formatar_momento_brasilia()
        corpo = (
            f"`👤` **Membro:** {membro.mention}\n"
            f"- **Cargo:** `{cargo}`\n"
            f"- **Tipo:** `voluntária`\n"
            f"- **Motivo:** {motivo}\n"
            f"`🕐` **Aberto em:** `{momento}`\n"
            f"`📌` **Status:** 🟡 **Pendente** — aguardando sua confirmação."
        )

        view = ViewConfirmarEnvioDemissao(
            membro_id=membro.id,
            motivo=motivo,
            cargo=cargo,
            corpo=corpo,
            url_thumb=membro.display_avatar.url,
        )
        await responder_view(
            interacao,
            view,
            ephemeral=True,
        )


class ViewConfirmarEnvioDemissao(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        *,
        membro_id: int,
        motivo: str,
        cargo: str,
        corpo: str,
        url_thumb: str,
    ):
        super().__init__(timeout=300)
        self.membro_id = membro_id
        self.motivo = motivo
        self.cargo = cargo

        linha = discord.ui.ActionRow()
        botao = discord.ui.Button(
            label="Confirmar desligamento",
            style=discord.ButtonStyle.danger,
            emoji="✅",
        )
        botao.callback = self._ao_confirmar
        linha.add_item(botao)

        self.add_item(
            discord.ui.Container(
                discord.ui.Section(
                    "# 🔍 Confirmar solicitação",
                    corpo,
                    accessory=discord.ui.Thumbnail(url_thumb),
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha,
                discord.ui.TextDisplay(
                    "-# Ao confirmar, o pedido vai para a **diretoria** analisar."
                ),
                accent_color=discord.Color.orange(),
            )
        )

    async def _ao_confirmar(self, interacao: discord.Interaction):
        if interacao.user.id != self.membro_id:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Só quem abriu o pedido pode confirmar."],
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

        pendente = await obter_pedido_pendente(interacao.user.id)
        if pendente is not None:
            await responder_aviso(
                interacao,
                titulo="Pedido já em análise",
                linhas=[f"Já existe o pedido `#{pendente.id}` pendente."],
            )
            return

        try:
            registro = await criar_solicitacao(
                membro=interacao.user,
                motivo=self.motivo,
            )
            postou = await publicar_pedido_diretoria(
                interacao.guild,
                membro=interacao.user,
                registro=registro,
            )
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Falha ao criar solicitação de demissão",
                erro,
                contexto="ViewConfirmarEnvioDemissao._ao_confirmar",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Erro ao enviar pedido",
                linhas=["A equipe foi avisada no log de erros."],
            )
            return

        await responder_sucesso(
            interacao,
            titulo="Pedido enviado",
            linhas=[
                f"Solicitação `#{registro.id}` registrada como **pendente**.",
                "A diretoria vai analisar o desligamento.",
                (
                    "Card postado no canal de aprovação."
                    if postou
                    else "Aviso: canal de aprovação não configurado "
                    "(CANAL_APROVAR_DEMISSAO)."
                ),
            ],
            delay=20,
        )


async def publicar_pedido_diretoria(
    guilda: discord.Guild,
    *,
    membro: discord.Member,
    registro,
) -> bool:
    """Publica o pedido pendente para decisão da diretoria e guarda sua mensagem.

    Retorna falso quando o canal não está configurado ou o Discord recusa o
    envio. No sucesso, grava no banco o canal e a mensagem do card para manter
    a ligação entre a solicitação persistida e a decisão visual.
    """
    canal_id = CANAIS.get("CANAL_APROVAR_DEMISSAO") or 0
    canal = guilda.get_channel(int(canal_id)) if canal_id else None
    if canal is None:
        return False

    momento = _formatar_momento_brasilia(registro.data_solicitacao)
    corpo = (
        f"`👤` **Membro:** {membro.mention} · **Pedido:** `#{registro.id}`\n"
        f"- **Cargo:** `{registro.cargo or '—'}`\n"
        f"- **Tipo:** `{registro.tipo_demissao}`\n"
        f"- **Advertências ativas:** `{registro.advertencias}`\n"
        f"- **Motivo:** {registro.motivo}\n"
        f"`🕐` **Aberto em:** `{momento}`\n"
        f"`📌` **Status:** 🟡 **Pendente** — aguardando diretoria"
    )

    view = ViewDecisaoDemissao(
        solicitacao_id=registro.id,
        corpo=corpo,
        url_thumb=membro.display_avatar.url,
    )
    try:
        mensagem = await canal.send(view=view)
        await marcar_mensagem_pedido(registro.id, canal.id, mensagem.id)
        return True
    except discord.HTTPException as erro:
        await enviar_erro_para_log_erros(
            guilda,
            "Falha ao postar pedido de demissão",
            erro,
            contexto="publicar_pedido_diretoria",
            usuario=membro,
        )
        return False


class ViewDecisaoDemissao(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, *, solicitacao_id: int, corpo: str, url_thumb: str):
        super().__init__(timeout=None)
        self.solicitacao_id = solicitacao_id

        # Só custom_id — o callback fica no on_interaction do cog
        # (evita processar a mesma interação duas vezes).
        linha = discord.ui.ActionRow()
        botao_ok = discord.ui.Button(
            label="Aprovar demissão",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"{CUSTOM_ID_APROVAR}{solicitacao_id}",
        )
        botao_no = discord.ui.Button(
            label="Recusar",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"{CUSTOM_ID_REPROVAR}{solicitacao_id}",
        )
        linha.add_item(botao_ok)
        linha.add_item(botao_no)

        self.add_item(
            discord.ui.Container(
                discord.ui.Section(
                    "# 📤 Solicitação de demissão",
                    corpo,
                    accessory=discord.ui.Thumbnail(url_thumb),
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha,
                discord.ui.TextDisplay("-# Aprovação exclusiva da diretoria"),
                accent_color=discord.Color.dark_red(),
            )
        )


async def processar_decisao_demissao(
    interacao: discord.Interaction,
    solicitacao_id: int,
    *,
    aprovada: bool,
) -> None:
    """Aplica a decisão da diretoria e executa os efeitos do desligamento.

    Protege a ação contra interações duplicadas e verifica o cargo decisor. Ao
    aprovar, atualiza o banco, remove cargos do membro e envia uma DM; ao
    recusar, mantém os cargos. Em ambos os casos, publica o log e remove o
    card de decisão do Discord quando possível.
    """
    if interacao.response.is_done():
        return

    if not isinstance(interacao.user, discord.Member) or interacao.guild is None:
        await responder_erro(
            interacao,
            titulo="Contexto inválido",
            linhas=["Use no servidor."],
        )
        return

    if not membro_e_diretoria(interacao.user):
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=["Apenas a **diretoria** pode decidir demissões."],
        )
        return

    await interacao.response.defer(ephemeral=True)

    registro, foi_agora = await decidir_demissao(
        solicitacao_id=solicitacao_id,
        aprovada=aprovada,
        diretor=interacao.user,
    )
    if registro is None:
        await responder_erro(
            interacao,
            titulo="Pedido não encontrado",
            linhas=[f"`#{solicitacao_id}`"],
        )
        return
    if not foi_agora:
        await responder_aviso(
            interacao,
            titulo="Já decidido",
            linhas=[f"Pedido `#{registro.id}` já está como `{registro.status}`."],
        )
        try:
            if interacao.message is not None:
                await interacao.message.delete()
        except discord.HTTPException as erro_em_processar_decisao_demissao:
            # Enfeite que falhou: atualizar o card do pedido de demissao.
            # A acao principal ja tinha dado certo, entao so registro.
            ignorar_falha_cosmetica(
                erro_em_processar_decisao_demissao,
                o_que_falhou="atualizar o card do pedido de demissao",
            )
        return

    membro = interacao.guild.get_member(registro.discord_id)
    if membro is None:
        try:
            membro = await interacao.guild.fetch_member(registro.discord_id)
        except discord.HTTPException:
            membro = None

    if aprovada:
        if membro is not None:
            ok, detalhe = await aplicar_cargos_demissao(
                membro,
                executor=interacao.user,
                motivo=registro.motivo,
            )
            if not ok:
                await enviar_erro_para_log_erros(
                    interacao.guild,
                    "Demissão aprovada mas falha nos cargos",
                    RuntimeError(detalhe),
                    contexto="processar_decisao_demissao.cargos",
                    usuario=interacao.user,
                )
            await enviar_dm_card(
                membro,
                titulo="🏥 CMS Valley — Comunicado Oficial",
                linhas=[
                    f"O membro **{registro.membro_nome}** encerrou suas atividades "
                    f"como **{registro.cargo or 'membro'}**.",
                    "",
                    "Agradecemos pelos serviços prestados e desejamos sucesso "
                    "em seus próximos desafios.",
                    "",
                    "— Diretoria CMS Valley",
                ],
                cor=COR_INFO,
                guilda=interacao.guild,
            )
        await responder_sucesso(
            interacao,
            titulo="Demissão aprovada",
            linhas=[
                f"Pedido `#{registro.id}` aprovado.",
                "Cargos removidos · restou **Visitantes**."
                if membro
                else "Membro fora do servidor — status atualizado mesmo assim.",
            ],
            delay=15,
        )
    else:
        if membro is not None:
            await enviar_dm_card(
                membro,
                titulo="Demissão não aprovada",
                linhas=[
                    f"Seu pedido `#{registro.id}` foi **recusado** pela diretoria.",
                    "Seus cargos foram mantidos. Fale com a equipe se precisar.",
                ],
                cor=COR_AVISO,
                guilda=interacao.guild,
            )
        await responder_aviso(
            interacao,
            titulo="Demissão recusada",
            linhas=[f"Pedido `#{registro.id}` marcado como **negada**."],
            delay=12,
        )

    await publicar_log_demissao(
        interacao.guild,
        registro=registro,
        diretor=interacao.user,
        aprovada=aprovada,
        membro=membro,
    )

    try:
        if interacao.message is not None:
            await interacao.message.delete()
    except discord.HTTPException as erro_em_processar_decisao_demissao:
        # Enfeite que falhou: atualizar o card do pedido de demissao.
        # A acao principal ja tinha dado certo, entao so registro.
        ignorar_falha_cosmetica(
            erro_em_processar_decisao_demissao,
            o_que_falhou="atualizar o card do pedido de demissao",
        )


async def publicar_log_demissao(
    guilda: discord.Guild,
    *,
    registro,
    diretor: discord.Member,
    aprovada: bool,
    membro: discord.Member | None,
) -> None:
    """Registra no canal de log o resultado administrativo da demissão.

    Inclui dados congelados do pedido e identifica diretoria e membro, mesmo
    quando o membro já saiu da guilda. Falhas ao publicar são tratadas como
    cosméticas para não desfazer uma decisão que já foi gravada no banco.
    """
    canal_id = CANAIS.get("LOG_DEMISSAO") or 0
    canal = guilda.get_channel(int(canal_id)) if canal_id else None
    if canal is None:
        return

    status = "✅ Aprovada" if aprovada else "❌ Negada"
    efetiva = (
        _formatar_momento_brasilia(registro.data_efetiva)
        if registro.data_efetiva
        else "—"
    )
    mencao = membro.mention if membro else f"`{registro.discord_id}`"
    corpo = (
        f"- **Membro:** {mencao} (`{registro.discord_id}`)\n"
        f"- **Nome no pedido:** `{registro.membro_nome}`\n"
        f"- **Cargo:** `{registro.cargo or '—'}`\n"
        f"- **Tipo:** `{registro.tipo_demissao}`\n"
        f"- **Status:** {status}\n"
        f"- **Pedido:** `#{registro.id}`\n"
        f"- **Advertências (na época):** `{registro.advertencias}`\n"
        f"- **Motivo:** {registro.motivo[:400]}\n"
        f"- **Diretoria:** {diretor.mention}\n"
        f"- **Solicitado em:** "
        f"`{_formatar_momento_brasilia(registro.data_solicitacao)}`\n"
        f"- **Efetiva em:** `{efetiva}`"
    )
    url = membro.display_avatar.url if membro else None
    componentes: list = [
        discord.ui.TextDisplay(f"# 📤 Demissão — {status}"),
    ]
    if url:
        componentes.append(
            discord.ui.Section(
                corpo,
                accessory=discord.ui.Thumbnail(url),
            )
        )
    else:
        componentes.append(discord.ui.TextDisplay(corpo))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            *componentes,
            accent_color=discord.Color.green() if aprovada else discord.Color.red(),
        )
    )
    try:
        await canal.send(view=view)
    except discord.HTTPException as erro_em_publicar_log_demissao:
        # Enfeite que falhou: publicar o log da demissao.
        # A acao principal ja tinha dado certo, entao so registro.
        ignorar_falha_cosmetica(
            erro_em_publicar_log_demissao,
            o_que_falhou="publicar o log da demissao",
        )


def view_painel_demissao(guilda: discord.Guild | None = None) -> PainelDemissaoLayout:
    """Cria o painel persistente usando ícone da guilda quando ele está disponível."""
    return PainelDemissaoLayout(guilda=guilda)
