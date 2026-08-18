"""Views e botões dos casos de baú."""

from __future__ import annotations

import discord

from src.bau.bau_service import (
    formatar_bloco_itens_yaml,
    ler_itens_do_caso,
    resolver_caso,
)
from src.config import (
    CANAIS,
    GUILD_ID_VALLEY,
    PRAZO_DEVOLUCAO_BAU_MINUTOS,
)
from src.database.conexao import async_session
from src.database.models import CasoBau
from src.plantao.plantao_permissoes import e_diretoria
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
    ignorar_falha_cosmetica,
)
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
    responder_sucesso,
)


def _pode_abrir_ocorrencia_valley(caso: CasoBau) -> bool:
    """
    Valley liberado quando:
    - caso é grave (camada 2 em algum item), ou
    - prazo de 30 min estourou (PRAZO_ESTOURADO)
    """
    if caso.status in ("RESOLVIDO", "IGNORADO", "PUNIDO"):
        return False
    return bool(caso.e_grave) or caso.status == "PRAZO_ESTOURADO"


async def atualizar_mensagem_alerta_caso(
    mensagem: discord.Message | None,
    caso: CasoBau,
    *,
    guild: discord.Guild | None,
) -> None:
    """Re-renderiza o card do alerta com status atual (desativa botões se fechado)."""
    if mensagem is None or guild is None:
        return
    try:
        await mensagem.edit(view=ViewCasoBau.montar_layout_alerta(caso, guild=guild))
    except discord.HTTPException as erro_em_atualizar_mensagem_alerta_caso:
        # Enfeite que falhou: atualizar a mensagem de alerta do caso.
        # A acao principal ja tinha dado certo, entao so registro.
        ignorar_falha_cosmetica(
            erro_em_atualizar_mensagem_alerta_caso,
            o_que_falhou="atualizar a mensagem de alerta do caso",
        )


class ViewCasoBau(LoggingViewMixin, discord.ui.LayoutView):
    """Card persistente no CANAL_ALERTA_BAU — um por passaporte, itens agregados."""

    def __init__(self, caso_id: int):
        super().__init__(timeout=None)
        self.caso_id = caso_id

    @staticmethod
    def montar_layout_alerta(
        caso: CasoBau,
        *,
        guild: discord.Guild,
        limite_1: int = 0,
        limite_2: int | None = None,
    ) -> discord.ui.LayoutView:
        """Monta o card persistente com ações compatíveis com o estado do caso.

        Exibe a dívida agregada e escolhe cor, textos e botões conforme a
        gravidade e o prazo. Os parâmetros de limite são preservados para
        chamadas antigas, embora a apresentação atual derive seus dados do caso.
        """
        # limite_1/limite_2 mantidos na assinatura por compatibilidade com
        # logger/listener
        mapa_itens = ler_itens_do_caso(caso)
        bloco_itens = formatar_bloco_itens_yaml(mapa_itens)

        cor = (
            discord.Color.red()
            if caso.e_grave or caso.status in ("PUNIDO", "PRAZO_ESTOURADO")
            else discord.Color.orange()
        )
        mencao = (
            f"<@{caso.discord_id}>" if caso.discord_id else "_Discord não vinculado_"
        )

        if caso.status == "PRAZO_ESTOURADO":
            gravidade = "⏰ PRAZO ESTOURADO — ocorrência Valley liberada"
        elif caso.e_grave:
            gravidade = "🔴 GRAVE (camada 2 em um ou mais itens)"
        else:
            gravidade = "🟠 Limite diário excedido (camada 1)"

        texto = (
            f"# 📦 REGISTRO DE CAIXA — BAÚ\n"
            f"> **{gravidade}**\n\n"
            f"> **🆔 FiveM ID:** `{caso.id_fivem}`\n"
            f"> **📡 Membro:** {mencao}\n"
            f"> **👤 Nome na Cidade:** `{caso.nome_cidade or '—'}`\n\n"
            f"## 🍱 ITENS (dívida do ciclo / pendência)\n"
            f"{bloco_itens}\n"
            f"Total de unidades: **{caso.quantidade_atual}**\n"
            f"Prazo: **{PRAZO_DEVOLUCAO_BAU_MINUTOS} min** · Caso `#{caso.id}` · "
            f"`{caso.status}`"
        )
        if caso.dm_falhou:
            texto += "\n\n⚠️ **DM falhou** — avisar o membro no servidor."

        view = ViewCasoBau(caso_id=caso.id)
        caso_fechado = caso.status in ("RESOLVIDO", "IGNORADO", "PUNIDO")
        liberar_valley = _pode_abrir_ocorrencia_valley(caso)

        linha = discord.ui.ActionRow()

        b_ok = discord.ui.Button(
            label="Devolveu",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"bau:devolveu:{caso.id}",
            disabled=caso_fechado,
        )
        b_ok.callback = view._ao_devolveu
        linha.add_item(b_ok)

        if liberar_valley and not caso_fechado:
            canal_ticket_valley = CANAIS.get("CANAL_TICKET_VALLEY") or 0
            if canal_ticket_valley and GUILD_ID_VALLEY:
                url_ticket = f"https://discord.com/channels/{GUILD_ID_VALLEY}/{canal_ticket_valley}"
                b_ocor = discord.ui.Button(
                    label="Abrir Ocorrência (Cidade)",
                    style=discord.ButtonStyle.link,
                    emoji="📌",
                    url=url_ticket,
                )
                linha.add_item(b_ocor)
            else:
                b_ocor = discord.ui.Button(
                    label="Abrir Ocorrência",
                    style=discord.ButtonStyle.danger,
                    emoji="📌",
                    custom_id=f"bau:ocorrencia:{caso.id}",
                    disabled=False,
                )
                b_ocor.callback = view._ao_ocorrencia
                linha.add_item(b_ocor)
        else:
            b_ocor = discord.ui.Button(
                label="Abrir Ocorrência",
                style=discord.ButtonStyle.danger,
                emoji="📌",
                custom_id=f"bau:ocorrencia:{caso.id}",
                disabled=caso_fechado or not liberar_valley,
            )
            b_ocor.callback = view._ao_ocorrencia
            linha.add_item(b_ocor)

        b_ign = discord.ui.Button(
            label="Justificar/Ignorar",
            style=discord.ButtonStyle.secondary,
            emoji="🔕",
            custom_id=f"bau:ignorar:{caso.id}",
            disabled=caso_fechado,
        )
        b_ign.callback = view._ao_ignorar
        linha.add_item(b_ign)

        if caso_fechado:
            texto += "\n\n-# Caso **" + str(caso.status) + "** — botões desativados."
        elif liberar_valley:
            texto += (
                "\n\n⚠️ **Abra ocorrência no Discord da cidade** (ticket Valley) "
                "para formalizar o excesso."
            )
        else:
            texto += (
                "\n\n-# Dentro do prazo: membro deve devolver. "
                "Após **30 min** sem devolução, o botão de ocorrência Valley é "
                "liberado."
            )

        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(texto),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha,
                accent_color=cor,
            )
        )
        return view

    async def _ao_devolveu(self, interacao: discord.Interaction):
        async with async_session() as sessao:
            caso_atual = await sessao.get(CasoBau, self.caso_id)
        if caso_atual is not None and caso_atual.status in (
            "RESOLVIDO",
            "IGNORADO",
            "PUNIDO",
        ):
            await responder_aviso(
                interacao,
                titulo="Caso já encerrado",
                linhas=[f"Status atual: `{caso_atual.status}`."],
            )
            return
        caso = await resolver_caso(
            self.caso_id,
            por_discord_id=interacao.user.id,
            status="RESOLVIDO",
        )
        if caso is None:
            await responder_erro(
                interacao, titulo="Caso", linhas=["Caso não encontrado."]
            )
            return
        await responder_sucesso(
            interacao,
            titulo="Caso resolvido",
            linhas=[f"Caso `#{self.caso_id}` marcado como **devolvido**."],
        )
        await atualizar_mensagem_alerta_caso(
            interacao.message,
            caso,
            guild=interacao.guild,
        )

    async def _ao_ocorrencia(self, interacao: discord.Interaction):
        async with async_session() as sessao:
            caso = await sessao.get(CasoBau, self.caso_id)

        if caso is None:
            await responder_erro(
                interacao, titulo="Caso", linhas=["Caso não encontrado."]
            )
            return

        if not _pode_abrir_ocorrencia_valley(caso):
            await responder_aviso(
                interacao,
                titulo="Ainda no prazo",
                linhas=[
                    f"O caso `#{self.caso_id}` ainda está dentro dos "
                    f"**{PRAZO_DEVOLUCAO_BAU_MINUTOS} minutos** de devolução.",
                    "O botão de ocorrência Valley libera após o prazo "
                    "(ou imediatamente se for **camada 2 / grave**).",
                ],
            )
            return

        canal_ticket_valley = CANAIS.get("CANAL_TICKET_VALLEY") or 0
        url_ticket = (
            f"https://discord.com/channels/{GUILD_ID_VALLEY}/{canal_ticket_valley}"
            if canal_ticket_valley and GUILD_ID_VALLEY
            else None
        )
        linhas = [
            f"Caso `#{self.caso_id}` — abra ticket no Discord da **cidade**.",
            "Formalize a ocorrência com os itens listados no card.",
        ]
        if url_ticket:
            linhas.append(f"[Abrir ticket Valley]({url_ticket})")
        await responder_aviso(
            interacao,
            titulo="Ocorrência — Cidade",
            linhas=linhas,
        )

    async def _ao_ignorar(self, interacao: discord.Interaction):
        membro = interacao.user
        if not isinstance(membro, discord.Member) or not e_diretoria(membro):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    "Apenas **Diretoria+** pode justificar ou ignorar um caso de baú.",
                ],
            )
            return
        await interacao.response.send_modal(ModalJustificarCaso(caso_id=self.caso_id))


class ModalJustificarCaso(
    LoggingModalMixin, discord.ui.Modal, title="🔕 Justificar / Ignorar"
):
    motivo = discord.ui.TextInput(
        label="Motivo obrigatório",
        style=discord.TextStyle.paragraph,
        placeholder="Ex.: autorização da diretoria para evento…",
        required=True,
        max_length=500,
    )

    def __init__(self, caso_id: int):
        super().__init__()
        self.caso_id = caso_id

    async def on_submit(self, interacao: discord.Interaction):
        """Valida a diretoria e encerra o caso como ignorado com justificativa.

        Reconsulta o caso para não sobrescrever uma decisão recente, salva o
        motivo no banco e responde ao solicitante. Depois tenta obter o card
        original para desativar suas ações e refletir a decisão no Discord.
        """
        membro = interacao.user
        if not isinstance(membro, discord.Member) or not e_diretoria(membro):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    "Apenas **Diretoria+** pode justificar ou ignorar um caso de baú.",
                ],
            )
            return

        async with async_session() as sessao:
            caso_atual = await sessao.get(CasoBau, self.caso_id)
        if caso_atual is not None and caso_atual.status in (
            "RESOLVIDO",
            "IGNORADO",
            "PUNIDO",
        ):
            await responder_aviso(
                interacao,
                titulo="Caso já encerrado",
                linhas=[f"Status atual: `{caso_atual.status}`."],
            )
            return

        caso = await resolver_caso(
            self.caso_id,
            por_discord_id=interacao.user.id,
            status="IGNORADO",
            motivo_ignore=self.motivo.value.strip(),
        )
        if caso is None:
            await responder_erro(
                interacao, titulo="Caso", linhas=["Caso não encontrado."]
            )
            return

        await responder_sucesso(
            interacao,
            titulo="Caso ignorado",
            linhas=[f"Caso `#{self.caso_id}` ignorado com justificativa registrada."],
        )

        mensagem_alerta = interacao.message
        if mensagem_alerta is None and caso.canal_alerta_message_id and interacao.guild:
            canal_alerta = interacao.guild.get_channel(
                CANAIS.get("CANAL_ALERTA_BAU") or 0
            )
            if canal_alerta is not None:
                try:
                    mensagem_alerta = await canal_alerta.fetch_message(
                        caso.canal_alerta_message_id
                    )
                except discord.HTTPException:
                    mensagem_alerta = None

        await atualizar_mensagem_alerta_caso(
            mensagem_alerta,
            caso,
            guild=interacao.guild,
        )


class ViewDmDevolucao(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, caso_id: int, guild_id: int):
        super().__init__(timeout=None)
        self.caso_id = caso_id
        canal_tickets = CANAIS.get("CANAL_TICKETS_BAU") or 0
        url = (
            f"https://discord.com/channels/{guild_id}/{canal_tickets}"
            if canal_tickets
            else None
        )
        texto = (
            f"# 📦 Aviso do Baú — CMS Valley\n\n"
            f"Você excedeu o limite diário de retiradas do baú.\n"
            f"Devolva o excesso em até **{PRAZO_DEVOLUCAO_BAU_MINUTOS} minutos** "
            f"abrindo um ticket e iniciando a devolução.\n"
            f"Caso `#{caso_id}`."
        )
        linha = discord.ui.ActionRow()
        if url:
            botao = discord.ui.Button(
                label="Iniciar Devolução",
                style=discord.ButtonStyle.link,
                url=url,
                emoji="📥",
            )
            linha.add_item(botao)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(texto),
                linha
                if url
                else discord.ui.TextDisplay(
                    "-# Configure CANAL_TICKETS_BAU no config."
                ),
                accent_color=discord.Color.orange(),
            )
        )
