"""Views do canal de finanças (botão Pagamento realizado).

Solicitações usam Components V2 (LayoutView + Container), no padrão do projeto.
O botão tem custom_id fixo para continuar funcionando após restart.

Fluxo do clique em Pagamento realizado (troca de moedas):
1. Diretoria+ clica no botão
2. O bot carrega o beneficiário pelo message_id no banco
3. Pede o comprovante no canal (prazo de 5 minutos)
4. Aceita só imagem ou PDF; outros tipos são ignorados até o prazo acabar
5. Baixa os bytes do anexo (cópia local — não depende da CDN)
6. Marca a solicitação como paga no card e no banco
7. Envia DM ao beneficiário: card formatado + comprovante em anexo
8. Apaga a mensagem do comprovante no canal de finanças após 10 segundos
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

import discord

from src.plantao.plantao_permissoes import e_diretoria
from src.utils.error_handling import (
    LoggingViewMixin,
    enviar_erro_para_log_erros,
)
from src.utils.mensagens import (
    editar_mensagem_original,
    excluir_mensagem,
    responder_aviso,
    responder_erro,
    responder_info,
    responder_sucesso,
)

logger = logging.getLogger(__name__)

CUSTOM_ID_PAGAMENTO_REALIZADO = "financas:pagamento_realizado"

# Tempo máximo para a diretoria enviar o comprovante após clicar no botão.
PRAZO_COMPROVANTE_SEGUNDOS = 5 * 60

# Depois da DM, apaga o comprovante do canal de finanças (já foi copiado).
SEGUNDOS_ATE_APAGAR_COMPROVANTE_NO_CANAL = 10

# Tipos aceitos no comprovante (extensão e content-type).
EXTENSOES_COMPROVANTE_OK = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".pdf",
}
TIPOS_MIME_COMPROVANTE_OK = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
    "application/pdf",
}


def _montar_botao_pagamento(*, ja_pago: bool) -> discord.ui.Button:
    botao = discord.ui.Button(
        label="Pagamento realizado" if not ja_pago else "Pago ✓",
        style=(
            discord.ButtonStyle.success
            if not ja_pago
            else discord.ButtonStyle.secondary
        ),
        emoji="✅",
        custom_id=CUSTOM_ID_PAGAMENTO_REALIZADO,
        disabled=ja_pago,
    )
    return botao


def _extrair_discord_id_do_corpo(corpo: str) -> int | None:
    """
    Lê o Discord ID gravado no corpo da solicitação de troca de moedas.

    Formato esperado no texto: Discord `123456789012345678`.
    Usado só como reserva quando o banco não tem o registro.
    """
    if not corpo:
        return None
    encontrado = re.search(r"Discord\s+`(\d{15,20})`", corpo)
    if encontrado is None:
        return None
    try:
        return int(encontrado.group(1))
    except ValueError:
        return None


def anexo_e_comprovante_valido(anexo: discord.Attachment) -> bool:
    """
    Aceita imagem (png/jpg/webp/gif) ou PDF.

    Checa extensão do nome e, se existir, o content_type informado pelo Discord.
    """
    nome = (anexo.filename or "").lower()
    extensao = Path(nome).suffix
    if extensao in EXTENSOES_COMPROVANTE_OK:
        return True

    tipo = (anexo.content_type or "").lower().split(";")[0].strip()
    if tipo in TIPOS_MIME_COMPROVANTE_OK:
        return True
    return False


def _primeiro_anexo_valido(
    mensagem: discord.Message,
) -> discord.Attachment | None:
    for anexo in mensagem.attachments:
        if anexo_e_comprovante_valido(anexo):
            return anexo
    return None


class ViewSolicitacaoFinancasCard(LoggingViewMixin, discord.ui.LayoutView):
    """
    Card Components V2 de solicitação financeira (padrão CardView / LogContainer).

    - titulo + corpo em TextDisplay
    - rodapé: nome da guilda + timestamp
    - botão Pagamento realizado (persistente)
    - discord_id_beneficiario: quem recebe a DM com o comprovante
    """

    def __init__(
        self,
        *,
        titulo: str,
        corpo: str,
        guild: discord.Guild | None,
        cor: discord.Color = discord.Color.dark_gold(),
        ja_pago: bool = False,
        pago_por_mencao: str | None = None,
        discord_id_beneficiario: int | None = None,
    ):
        super().__init__(timeout=None)
        self.titulo = titulo
        self.corpo = corpo
        self.guild_ref = guild
        self.cor = cor
        self.ja_pago = ja_pago
        self.discord_id_beneficiario = discord_id_beneficiario
        if self.discord_id_beneficiario is None:
            self.discord_id_beneficiario = _extrair_discord_id_do_corpo(corpo)

        momento = int(datetime.now(timezone.utc).timestamp())
        nome_guilda = guild.name if guild is not None else "CENTRO MÉDICO SUL VALLEY"
        rodape = f"-# {nome_guilda} • <t:{momento}:f>"

        corpo_final = corpo
        if ja_pago:
            corpo_final = (
                corpo
                + "\n\n"
                + "-# ✅ **PAGAMENTO REALIZADO**"
                + (f" por {pago_por_mencao}" if pago_por_mencao else "")
                + f" · <t:{momento}:f>"
            )

        linha_botoes = discord.ui.ActionRow()
        botao = _montar_botao_pagamento(ja_pago=ja_pago)
        botao.callback = self._ao_marcar_pago
        linha_botoes.add_item(botao)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(f"# {titulo}"),
                discord.ui.TextDisplay(corpo_final),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha_botoes,
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(rodape),
                accent_color=discord.Color.green() if ja_pago else cor,
            )
        )

    async def _ao_marcar_pago(self, interacao: discord.Interaction):
        try:
            concluido = await processar_pagamento_realizado(
                interacao,
                titulo_fallback=self.titulo,
                corpo_fallback=self.corpo,
                discord_id_fallback=self.discord_id_beneficiario,
                ja_pago_na_view=self.ja_pago,
            )
            if concluido:
                self.ja_pago = True
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro ao marcar pagamento (finanças)",
                erro,
                contexto="ViewSolicitacaoFinancasCard._ao_marcar_pago",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=["Falha ao marcar pagamento. A equipe foi notificada."],
            )


async def processar_pagamento_realizado(
    interacao: discord.Interaction,
    *,
    titulo_fallback: str | None = None,
    corpo_fallback: str | None = None,
    discord_id_fallback: int | None = None,
    ja_pago_na_view: bool = False,
) -> bool:
    """
    Fluxo único de Pagamento realizado (card V2 ou botão persistente).

    1) Confere permissão e se já está pago (view + banco)
    2) Pede comprovante (imagem ou PDF) por até 5 minutos
    3) Atualiza card e banco
    4) Envia DM com cópia do comprovante

    Devolve True só quando o card do canal foi marcado como pago.
    """
    from src.financas.financas_service import (
        buscar_solicitacao_por_mensagem,
        marcar_solicitacao_como_paga,
    )

    membro = interacao.user
    if not isinstance(membro, discord.Member) or not e_diretoria(membro):
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=["Apenas **Diretoria+** pode marcar pagamento como realizado."],
        )
        return False

    mensagem_do_canal = interacao.message
    mensagem_id = mensagem_do_canal.id if mensagem_do_canal is not None else None

    registro = None
    if mensagem_id is not None:
        registro = await buscar_solicitacao_por_mensagem(mensagem_id)

    if ja_pago_na_view or (registro is not None and registro.status == "pago"):
        await responder_aviso(
            interacao,
            titulo="Já pago",
            linhas=["Esta solicitação já foi marcada como paga."],
            delay=6,
        )
        return False

    titulo = (
        (registro.titulo if registro else None)
        or titulo_fallback
        or ("🏥 PAGAMENTO — TROCA DE MOEDAS")
    )
    corpo = (
        (registro.corpo if registro else None)
        or corpo_fallback
        or ("_Solicitação de troca de moedas._")
    )
    id_beneficiario = (
        registro.discord_id_beneficiario
        if registro is not None
        else discord_id_fallback
    )
    if id_beneficiario is None:
        id_beneficiario = _extrair_discord_id_do_corpo(corpo)

    if not interacao.response.is_done():
        await interacao.response.defer(ephemeral=True)

    minutos = PRAZO_COMPROVANTE_SEGUNDOS // 60
    await responder_info(
        interacao,
        titulo="Comprovante necessário",
        linhas=[
            "Envie **neste canal** o comprovante do pagamento in-game.",
            "Formatos aceitos: **PNG, JPG, WEBP, GIF ou PDF**.",
            f"Prazo: **{minutos} minutos**.",
            "Só conta mensagem **sua** com **anexo válido**.",
            "Arquivos de outro tipo são ignorados até o prazo acabar.",
        ],
        delay=None,
    )

    bot = interacao.client
    canal_id = interacao.channel_id
    autor_id = membro.id

    def checagem_comprovante(mensagem: discord.Message) -> bool:
        if mensagem.author.id != autor_id:
            return False
        if mensagem.channel.id != canal_id:
            return False
        return _primeiro_anexo_valido(mensagem) is not None

    try:
        mensagem_comprovante = await bot.wait_for(
            "message",
            timeout=PRAZO_COMPROVANTE_SEGUNDOS,
            check=checagem_comprovante,
        )
    except TimeoutError:
        await responder_aviso(
            interacao,
            titulo="Prazo esgotado",
            linhas=[
                f"Nenhum comprovante válido chegou em **{minutos} minutos**.",
                "A solicitação **permanece pendente**.",
                "Clique de novo em **Pagamento realizado** quando tiver "
                "uma imagem ou PDF.",
            ],
            delay=20,
        )
        return False

    anexo = _primeiro_anexo_valido(mensagem_comprovante)
    if anexo is None:
        await responder_erro(
            interacao,
            titulo="Comprovante inválido",
            linhas=[
                "O anexo não é uma imagem nem PDF.",
                "Envie PNG, JPG, WEBP, GIF ou PDF e tente de novo.",
            ],
        )
        return False

    nome_arquivo = anexo.filename or "comprovante.png"
    try:
        bytes_do_comprovante = await anexo.read()
    except (discord.HTTPException, OSError) as erro_leitura:
        logger.warning(
            "Falha ao baixar comprovante de pagamento: %s",
            erro_leitura,
        )
        await responder_erro(
            interacao,
            titulo="Falha no comprovante",
            linhas=[
                "Não consegui baixar o arquivo enviado.",
                "Tente de novo com outra imagem ou outro PDF.",
            ],
        )
        return False

    if not bytes_do_comprovante:
        await responder_erro(
            interacao,
            titulo="Arquivo vazio",
            linhas=["O anexo veio sem conteúdo. Envie outro comprovante."],
        )
        return False

    guilda = interacao.guild
    view_paga = ViewSolicitacaoFinancasCard(
        titulo=titulo,
        corpo=corpo,
        guild=guilda,
        ja_pago=True,
        pago_por_mencao=membro.mention,
        discord_id_beneficiario=id_beneficiario,
    )

    try:
        if mensagem_do_canal is not None:
            await mensagem_do_canal.edit(view=view_paga)
        else:
            await editar_mensagem_original(
                interacao,
                view=view_paga,
            )
    except discord.HTTPException as erro_edit:
        await enviar_erro_para_log_erros(
            interacao.guild,
            "Falha HTTP ao editar solicitação de pagamento",
            erro_edit,
            contexto="edit_message pagamento realizado",
            usuario=membro,
        )
        await responder_erro(
            interacao,
            titulo="Falha ao atualizar",
            linhas=["Não foi possível marcar o pagamento nesta mensagem."],
        )
        return False

    if mensagem_id is not None:
        await marcar_solicitacao_como_paga(
            mensagem_id,
            pago_por_id=membro.id,
        )

    enviou_dm = False
    if id_beneficiario is not None and guilda is not None:
        enviou_dm = await _enviar_dm_com_comprovante(
            bot=bot,
            guilda=guilda,
            discord_id_beneficiario=id_beneficiario,
            titulo_solicitacao=titulo,
            corpo_solicitacao=corpo,
            bytes_do_comprovante=bytes_do_comprovante,
            nome_arquivo=nome_arquivo,
            pago_por=membro,
        )
    elif id_beneficiario is None:
        logger.warning(
            "Pagamento realizado sem discord_id do beneficiário (mensagem_id=%s)",
            mensagem_id,
        )

    # Comprovante no canal já foi copiado para a DM: remove depois de 10s.
    if enviou_dm:
        asyncio.create_task(
            excluir_mensagem(
                mensagem_comprovante,
                delay=SEGUNDOS_ATE_APAGAR_COMPROVANTE_NO_CANAL,
            )
        )

    if enviou_dm:
        await responder_sucesso(
            interacao,
            titulo="Pagamento realizado",
            linhas=[
                "Solicitação marcada como **pagamento realizado**.",
                "Comprovante enviado na **DM** do membro.",
                "A mensagem do comprovante neste canal some em "
                f"**{SEGUNDOS_ATE_APAGAR_COMPROVANTE_NO_CANAL} segundos**.",
            ],
            delay=12,
        )
    elif id_beneficiario is None:
        await responder_aviso(
            interacao,
            titulo="Pagamento realizado",
            linhas=[
                "Solicitação marcada como **pagamento realizado**.",
                "Não encontrei o membro no banco nem no card para enviar a DM.",
                "Envie o comprovante manualmente se precisar.",
            ],
            delay=15,
        )
    else:
        await responder_aviso(
            interacao,
            titulo="Pagamento realizado",
            linhas=[
                "Solicitação marcada como **pagamento realizado**.",
                "A **DM** do membro falhou (DM fechada ou erro de envio).",
                "Avise o membro manualmente se precisar.",
            ],
            delay=15,
        )
    return True


async def _enviar_dm_com_comprovante(
    *,
    bot: discord.Client,
    guilda: discord.Guild,
    discord_id_beneficiario: int,
    titulo_solicitacao: str,
    corpo_solicitacao: str,
    bytes_do_comprovante: bytes,
    nome_arquivo: str,
    pago_por: discord.Member,
) -> bool:
    """
    Resolve o usuário e envia card + cópia do comprovante na DM.

    A cópia é montada a partir dos bytes baixados no clique, para o membro
    guardar o arquivo na própria DM sem depender do link original da CDN.
    """
    from src.utils.notificacao import notificar_dm_pagamento_com_comprovante

    try:
        usuario = await bot.fetch_user(discord_id_beneficiario)
    except (discord.NotFound, discord.HTTPException) as erro_fetch:
        logger.warning(
            "Não achei usuário %s para DM de pagamento: %s",
            discord_id_beneficiario,
            erro_fetch,
        )
        return False

    return await notificar_dm_pagamento_com_comprovante(
        usuario,
        titulo_solicitacao=titulo_solicitacao,
        corpo_solicitacao=corpo_solicitacao,
        bytes_do_comprovante=bytes_do_comprovante,
        nome_arquivo=nome_arquivo,
        pago_por_mencao=pago_por.mention,
        guilda=guilda,
    )


class ViewBotaoPagamentoFinancas(LoggingViewMixin, discord.ui.View):
    """
    View clássica mínima só para registrar o custom_id no startup.

    Mensagens novas de troca de moedas usam ViewSolicitacaoFinancasCard.
    Depois de um restart, este botão persiste o custom_id e redireciona
    para o mesmo fluxo (banco + comprovante) quando a mensagem tiver
    registro de troca de moedas.

    Exceção documentada em AGENTS.md: View clássica para cliques em
    mensagens antigas já publicadas no canal de finanças.
    """

    def __init__(self, *, ja_pago: bool = False):
        super().__init__(timeout=None)
        botao = _montar_botao_pagamento(ja_pago=ja_pago)
        botao.callback = self._ao_marcar_pago_legado
        self.add_item(botao)

    async def _ao_marcar_pago_legado(self, interacao: discord.Interaction):
        from src.financas.financas_service import buscar_solicitacao_por_mensagem

        membro = interacao.user
        if not isinstance(membro, discord.Member) or not e_diretoria(membro):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas **Diretoria+** pode marcar pagamento como realizado."],
            )
            return

        mensagem = interacao.message
        texto_atual = (mensagem.content or "").strip() if mensagem else ""

        if "PAGAMENTO REALIZADO" in texto_atual.upper():
            await responder_aviso(
                interacao,
                titulo="Já pago",
                linhas=["Esta solicitação já foi marcada como paga."],
                delay=6,
            )
            return

        # Troca de moedas (card V2 ou registro no banco): fluxo completo
        if mensagem is not None:
            registro = await buscar_solicitacao_por_mensagem(mensagem.id)
            if registro is not None:
                await processar_pagamento_realizado(
                    interacao,
                    titulo_fallback=registro.titulo,
                    corpo_fallback=registro.corpo,
                    discord_id_fallback=registro.discord_id_beneficiario,
                    ja_pago_na_view=registro.status == "pago",
                )
                return

        # Ranking / lista legada em content: só marca o post como pago
        if texto_atual:
            carimbo = int(datetime.now(timezone.utc).timestamp())
            texto_novo = (
                texto_atual
                + "\n\n"
                + f"-# ✅ **PAGAMENTO REALIZADO** por {membro.mention} "
                + f"· <t:{carimbo}:f>"
            )
            view_paga = ViewBotaoPagamentoFinancas(ja_pago=True)
            try:
                await editar_mensagem_original(
                    interacao,
                    view=view_paga,
                    texto=texto_novo,
                )
            except discord.HTTPException as erro:
                await enviar_erro_para_log_erros(
                    interacao.guild,
                    "Falha ao marcar pagamento (legado content)",
                    erro,
                    contexto="ViewBotaoPagamentoFinancas legado",
                    usuario=membro,
                )
                await responder_erro(
                    interacao,
                    titulo="Falha ao atualizar",
                    linhas=["Não foi possível marcar o pagamento nesta mensagem."],
                )
                return
            await responder_sucesso(
                interacao,
                titulo="Pagamento realizado",
                linhas=["Solicitação marcada como **pagamento realizado**."],
                delay=8,
            )
            return

        # Card V2 sem registro no banco (pedido antigo, pré-persistência)
        await processar_pagamento_realizado(
            interacao,
            titulo_fallback="🏥 PAGAMENTO — TROCA DE MOEDAS",
            corpo_fallback="_Solicitação sem registro no banco._",
            discord_id_fallback=None,
            ja_pago_na_view=False,
        )


def view_persistente_financas() -> ViewBotaoPagamentoFinancas:
    """Registrar no startup (custom_id fixo)."""
    return ViewBotaoPagamentoFinancas(ja_pago=False)
