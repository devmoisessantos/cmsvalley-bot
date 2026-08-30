"""Painel persistente de compartilhamento de tela (Components V2)."""

from __future__ import annotations

import discord

from src.config import CARGOS_HIERARQUIA
from src.screenshare.screenshare_logger import (
    publicar_log_erro_screenshare,
    publicar_log_sala_criada,
)
from src.screenshare.screenshare_service import criar_sala
from src.utils.error_handling import LoggingViewMixin
from src.utils.mensagens import (
    responder_erro,
    responder_info,
    responder_sucesso,
)
from src.utils.permissions import membro_tem_algum_cargo


TEXTO_PAINEL = (
    "# Compartilhamento de tela — Centro Medico Sul\n\n"
    "Gere um link para transmitir sua tela pelo navegador.\n"
    "Quem abrir o link ve a tela em tempo real, sem instalar nada.\n\n"
    "**Como usar**\n"
    "1. Clique em **Gerar link**\n"
    "2. Abra o link no navegador (Chrome ou Edge)\n"
    "3. Escolha a tela, janela ou aba e confirme\n"
    "4. Envie o mesmo link para quem for assistir\n\n"
    "-# Disponivel para cargos da hierarquia do hospital. "
    "Em redes dificeis o relay TURN entra sozinho."
)


def membro_pode_compartilhar(membro: discord.Member) -> bool:
    """Libera quem tem cargo da hierarquia ou administrador do servidor."""
    if membro.guild_permissions.administrator:
        return True
    return membro_tem_algum_cargo(membro, CARGOS_HIERARQUIA)


class PainelScreenshareLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel fixo no canal de compartilhamento."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(timeout=None)
        self.guild = guild
        url_do_icone = guild.icon.url if guild and guild.icon else None

        if url_do_icone:
            cabecalho = discord.ui.Section(
                TEXTO_PAINEL,
                accessory=discord.ui.Thumbnail(url_do_icone),
            )
        else:
            cabecalho = discord.ui.TextDisplay(TEXTO_PAINEL)

        linha_de_botoes = discord.ui.ActionRow()
        botao_gerar = discord.ui.Button(
            label="Gerar link",
            style=discord.ButtonStyle.primary,
            custom_id="screenshare:painel:gerar",
        )
        botao_gerar.callback = self._ao_gerar_link
        linha_de_botoes.add_item(botao_gerar)

        botao_status = discord.ui.Button(
            label="Status do servico",
            style=discord.ButtonStyle.secondary,
            custom_id="screenshare:painel:status",
        )
        botao_status.callback = self._ao_status
        linha_de_botoes.add_item(botao_status)

        self.add_item(
            discord.ui.Container(
                cabecalho,
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha_de_botoes,
                accent_color=discord.Color.teal(),
            )
        )

    async def _ao_gerar_link(self, interacao: discord.Interaction):
        membro = interacao.user
        if not isinstance(membro, discord.Member):
            await responder_erro(
                interacao,
                titulo="Ambiente invalido",
                linhas=["Este painel so funciona dentro do servidor."],
            )
            return

        if not membro_pode_compartilhar(membro):
            await responder_erro(
                interacao,
                titulo="Sem permissao",
                linhas=[
                    "Voce precisa de um cargo da hierarquia do hospital "
                    "para gerar links de compartilhamento.",
                ],
            )
            return

        try:
            dados_da_sala = await criar_sala(
                nome_exibicao=membro.display_name,
            )
        except RuntimeError as erro_amigavel:
            await publicar_log_erro_screenshare(
                interacao.guild,
                membro,
                contexto="gerar_link",
                detalhe=str(erro_amigavel),
            )
            await responder_erro(
                interacao,
                titulo="Falha ao gerar link",
                linhas=[str(erro_amigavel)],
            )
            return
        except Exception as erro_inesperado:
            await publicar_log_erro_screenshare(
                interacao.guild,
                membro,
                contexto="gerar_link",
                detalhe=f"{type(erro_inesperado).__name__}: {erro_inesperado}",
            )
            await responder_erro(
                interacao,
                titulo="Falha ao gerar link",
                linhas=[
                    "Nao consegui gerar o link agora.",
                    "A administracao foi avisada.",
                ],
            )
            return

        codigo = dados_da_sala.get("code", "")
        link = dados_da_sala.get("invite_url", "")

        if interacao.guild is not None:
            await publicar_log_sala_criada(
                interacao.guild,
                membro,
                codigo=codigo,
                link=link,
            )

        await responder_sucesso(
            interacao,
            titulo="Link de compartilhamento",
            linhas=[
                f"Codigo: `{codigo}`",
                f"Link: {link}",
                "Abra o link no navegador e escolha o que transmitir.",
                "Quem for assistir usa o **mesmo link**.",
            ],
            delay=None,
        )

    async def _ao_status(self, interacao: discord.Interaction):
        from src.screenshare.screenshare_service import checar_saude

        ok, detalhe = await checar_saude()
        if not ok:
            await responder_erro(
                interacao,
                titulo="Servico indisponivel",
                linhas=[str(detalhe)],
            )
            return

        if isinstance(detalhe, dict):
            store = detalhe.get("store", "?")
            turn = detalhe.get("turn_configured", False)
            conexoes = detalhe.get("connections", 0)
            linhas = [
                f"Store: `{store}`",
                f"TURN configurado: `{'sim' if turn else 'nao'}`",
                f"Conexoes WS neste processo: `{conexoes}`",
            ]
        else:
            linhas = [str(detalhe)]

        await responder_info(
            interacao,
            titulo="Status do compartilhamento",
            linhas=linhas,
            delay=30,
        )
