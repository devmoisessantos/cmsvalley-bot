"""
Comandos administrativos do painel de status do servidor FiveM.

/status publicar — força a publicação ou atualização imediata do painel
/status testar — só consulta o FiveM e mostra o resultado (diagnóstico)
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from src.config import CANAIS
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.status_servidor.status_servidor_panel import montar_painel_status
from src.status_servidor.status_servidor_service import buscar_status_do_servidor
from src.utils.mensagens import responder_erro, responder_sucesso
from src.utils.permissions import esta_autorizado

registrador = logging.getLogger(__name__)

NOME_PAINEL = "status_servidor"


class StatusServidorCogs(commands.Cog):
    """
    Comandos de barra do domínio status_servidor.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="status",
        description="Publica ou testa o painel de status do servidor FiveM.",
    )
    @app_commands.describe(
        acao="O que fazer com o painel de status.",
    )
    @app_commands.choices(
        acao=[
            app_commands.Choice(name="publicar", value="publicar"),
            app_commands.Choice(name="testar", value="testar"),
        ]
    )
    @esta_autorizado()
    async def comando_status(
        self,
        interacao: discord.Interaction,
        acao: app_commands.Choice[str],
    ) -> None:
        """
        Publica o painel ou testa a consulta de status do FiveM.
        """
        if acao.value == "testar":
            await self._acao_testar(interacao)
            return

        if acao.value != "publicar":
            await responder_erro(
                interacao,
                titulo="Ação inválida",
                linhas=["Ação não reconhecida."],
            )
            return

        canal_id = CANAIS.get("CANAL_STATUS_SERVIDOR") or 0
        if not canal_id:
            await responder_erro(
                interacao,
                titulo="Canal não configurado",
                linhas=[
                    "O canal de status ainda não está em config.py.",
                    "Preencha CANAIS['CANAL_STATUS_SERVIDOR'] e reinicie.",
                ],
            )
            return

        canal = interacao.client.get_channel(canal_id)
        if canal is None:
            try:
                canal = await interacao.client.fetch_channel(canal_id)
            except Exception:
                await responder_erro(
                    interacao,
                    titulo="Canal não encontrado",
                    linhas=["Não encontrei o canal de status configurado."],
                )
                return

        await interacao.response.defer(ephemeral=True)

        try:
            dados = await buscar_status_do_servidor()
            view = montar_painel_status(dados)

            async with async_session() as sessao:
                resultado = await sessao.execute(
                    select(PainelPostado).where(
                        PainelPostado.nome_painel == NOME_PAINEL
                    )
                )
                registro = resultado.scalar_one_or_none()

                atualizou_existente = False

                if registro is not None:
                    (
                        _mensagem,
                        atualizou_existente,
                    ) = await self._tentar_atualizar_mensagem(
                        canal,
                        registro.message_id,
                        view,
                    )

                if not atualizou_existente:
                    mensagem_final = await canal.send(view=view)

                    if registro is not None:
                        registro.message_id = mensagem_final.id
                        registro.canal_id = canal.id
                        sessao.add(registro)
                    else:
                        novo = PainelPostado(
                            nome_painel=NOME_PAINEL,
                            canal_id=canal.id,
                            message_id=mensagem_final.id,
                        )
                        sessao.add(novo)

                    await sessao.commit()

            if dados.get("online"):
                resumo = f"ONLINE · {dados['jogadores']}/{dados['max_jogadores']}"
            else:
                resumo = f"OFFLINE · {dados.get('erro') or 'sem detalhe'}"

            if atualizou_existente:
                await responder_sucesso(
                    interacao,
                    titulo="Painel atualizado",
                    linhas=[
                        "Painel de status atualizado com sucesso.",
                        f"Leitura atual: **{resumo}**",
                    ],
                )
            else:
                await responder_sucesso(
                    interacao,
                    titulo="Painel publicado",
                    linhas=[
                        "Painel de status publicado no canal configurado.",
                        f"Leitura atual: **{resumo}**",
                        "Se ainda houver uma mensagem antiga, pode apagá-la.",
                    ],
                )
        except Exception as erro_capturado:
            registrador.exception(
                "Falha no comando /status publicar: %s",
                erro_capturado,
            )
            await responder_erro(
                interacao,
                titulo="Falha ao publicar",
                linhas=[
                    "Não consegui publicar o painel de status agora.",
                    f"Detalhe técnico: `{type(erro_capturado).__name__}: "
                    f"{erro_capturado}`",
                ],
            )

    async def _acao_testar(self, interacao: discord.Interaction) -> None:
        """
        Só testa a consulta FiveM e mostra o resultado no ephemeral.
        """
        await interacao.response.defer(ephemeral=True)

        try:
            dados = await buscar_status_do_servidor()
            if dados.get("online"):
                await responder_sucesso(
                    interacao,
                    titulo="Consulta FiveM OK",
                    linhas=[
                        "Status: **ONLINE**",
                        f"Jogadores: **{dados['jogadores']}** / "
                        f"**{dados['max_jogadores']}**",
                        f"Nome lido: {dados.get('nome') or '—'}",
                    ],
                )
            else:
                await responder_erro(
                    interacao,
                    titulo="Consulta FiveM falhou",
                    linhas=[
                        "O bot não alcançou o servidor daqui.",
                        f"Detalhe: {dados.get('erro') or 'desconhecido'}",
                        "Veja o log técnico do bot (warning de URL).",
                    ],
                )
        except Exception as erro_capturado:
            registrador.exception(
                "Falha no comando /status testar: %s",
                erro_capturado,
            )
            await responder_erro(
                interacao,
                titulo="Falha no teste",
                linhas=[
                    "Erro ao testar a consulta.",
                    f"`{type(erro_capturado).__name__}: {erro_capturado}`",
                ],
            )

    async def _tentar_atualizar_mensagem(
        self,
        canal: discord.abc.Messageable,
        message_id: int,
        view: discord.ui.LayoutView,
    ) -> tuple[discord.Message | None, bool]:
        """
        Tenta editar a mensagem existente.

        Devolve (mensagem, True) se editou.
        Devolve (None, False) se precisa republicar.
        """
        try:
            mensagem = await canal.fetch_message(message_id)
        except discord.NotFound:
            registrador.info(
                "Mensagem de status %s não existe mais.",
                message_id,
            )
            return None, False
        except discord.HTTPException as erro_http:
            registrador.warning(
                "Não consegui buscar a mensagem de status %s: %s",
                message_id,
                erro_http,
            )
            return None, False

        try:
            await mensagem.edit(view=view)
            return mensagem, True
        except discord.HTTPException as erro_edicao:
            registrador.warning(
                "Edição do painel de status falhou (%s). Vou apagar e republicar.",
                erro_edicao,
            )
            try:
                await mensagem.delete()
            except discord.HTTPException:
                pass
            return None, False


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatusServidorCogs(bot))
