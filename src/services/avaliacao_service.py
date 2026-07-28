import json
import discord
from datetime import datetime, timezone

from sqlalchemy import select

from src.config import CARGOS, CANAIS, TOTAL_PERGUNTAS_PROVA, NOTA_MINIMA_APROVACAO
from src.database.connection import async_session
from src.database.models import Recrutamento, Pergunta, RespostaProva
from src.utils.logger import log_mudanca_cargo
from src.utils.error_handling import LoggingViewMixin
from src.panels.aprovacao_panel import AprovacaoView


def barra_progresso(atual: int, total: int) -> str:
    preenchido = "▰" * atual
    vazio = "▱" * (total - atual)
    return f"{preenchido}{vazio}  `{atual}/{total}`"


async def iniciar_avaliacao(interaction: discord.Interaction):
    candidato = interaction.user
    guild = interaction.guild

    await interaction.response.defer(ephemeral=True)

    async with async_session() as session:
        resultado = await session.execute(
            select(Recrutamento).where(
                Recrutamento.discord_id_candidato == candidato.id,
                Recrutamento.status == "PROVA_LIBERADA",  # <-- antes era "ESTUDANDO"
            )
        )
        recrutamento = resultado.scalars().all()

        if recrutamento is None:
            await interaction.followup.send(
                "❌ Você não possui um recrutamento ativo em fase de estudo.", ephemeral=True
            )
            return

        if recrutamento.formulario_aberto:
            await interaction.followup.send(
                "❌ Sua avaliação já foi iniciada anteriormente e não pode ser reaberta. "
                "Caso tenha ocorrido um erro, procure um recrutador.",
                ephemeral=True,
            )
            return

        recrutamento.formulario_aberto = True
        recrutamento.status = "EM_PROVA"
        recrutamento.pergunta_atual = 0
        recrutamento.data_inicio_prova = datetime.now(timezone.utc)
        await session.commit()


    # 👇 NÃO troca mais cargo aqui — já foi trocado na liberação
    view = await montar_view_pergunta(numero=1, guild=guild)
    await interaction.response.send_message(view=view, ephemeral=True)



async def montar_view_pergunta(numero: int, guild: discord.Guild) -> "PerguntaLayoutView":
    async with async_session() as session:
        resultado = await session.execute(select(Pergunta).where(Pergunta.ordem == numero))
        pergunta = resultado.scalar_one()

    return PerguntaLayoutView(numero=numero, pergunta=pergunta, guild=guild)


class PerguntaLayoutView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, numero: int, pergunta: Pergunta, guild: discord.Guild):
        super().__init__(timeout=None)
        self.numero = numero
        self.pergunta = pergunta

        opcoes = json.loads(pergunta.opcoes)
        letras = ["A", "B", "C", "D"]

        self.select = discord.ui.Select(
            placeholder="Escolha uma resposta...",
            options=[
                discord.SelectOption(label=f"{letras[i]}) {texto}", value=letras[i])
                for i, texto in enumerate(opcoes)
            ],
        )
        self.select.callback = self.responder

        action_row = discord.ui.ActionRow()
        action_row.add_item(self.select)

        logo_url = guild.icon.url if guild.icon else None
        cabecalho = discord.ui.Section(
            "# 📋・PROVA — CENTRO MÉDICO SUL VALLEY\n\n",
            (
                "> Leia com atenção. Algumas questões exigem interpretação.\n"
                "> Responda corretamente às questões abaixo.\n"
                "> Cada pergunta possui apenas 1 alternativa correta.\n\n"
                "Avaliação iniciada! Você tem **1 hora** para concluir."
            ),
            accessory=discord.ui.Thumbnail(logo_url) if logo_url else discord.ui.Thumbnail("attachment://logo.png"),
        )
        container = discord.ui.Container(
            cabecalho,
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(f"# 📝 Pergunta {numero}/{TOTAL_PERGUNTAS_PROVA}"),
            discord.ui.TextDisplay(f"-# {barra_progresso(numero, TOTAL_PERGUNTAS_PROVA)}"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(pergunta.enunciado),
            action_row,
            accent_color=discord.Color.gold(),
        )
        self.add_item(container)

    async def responder(self, interaction: discord.Interaction):
        resposta = self.select.values[0]
        correta = resposta == self.pergunta.resposta_correta

        async with async_session() as session:
            resultado = await session.execute(
                select(Recrutamento).where(
                    Recrutamento.discord_id_candidato == interaction.user.id,
                    Recrutamento.status == "EM_PROVA",
                )
            )
            recrutamento = resultado.scalar_one()

            session.add(RespostaProva(
                recrutamento_id=recrutamento.id,
                numero_pergunta=self.numero,
                resposta_escolhida=resposta,
                correta=correta,
            ))
            recrutamento.pergunta_atual = self.numero
            await session.commit()

        if self.numero >= TOTAL_PERGUNTAS_PROVA:
            await interaction.response.edit_message(view=EnviarQuestionarioView())
        else:
            proxima_view = await montar_view_pergunta(numero=self.numero + 1, guild=interaction.guild)
            await interaction.response.edit_message(view=proxima_view)


class EnviarQuestionarioView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        self.botao = discord.ui.Button(label="Enviar Questionário", style=discord.ButtonStyle.success)
        self.botao.callback = self.enviar

        action_row = discord.ui.ActionRow()
        action_row.add_item(self.botao)

        container = discord.ui.Container(
            discord.ui.TextDisplay("# ✅ Questionário concluído"),
            discord.ui.TextDisplay(f"-# {barra_progresso(TOTAL_PERGUNTAS_PROVA, TOTAL_PERGUNTAS_PROVA)}"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay("Você respondeu todas as perguntas. Clique abaixo para enviar sua avaliação."),
            action_row,
            accent_color=discord.Color.gold(),
        )
        self.add_item(container)

    async def enviar(self, interaction: discord.Interaction):
        await finalizar_avaliacao(interaction)


async def finalizar_avaliacao(interaction: discord.Interaction):
    candidato = interaction.user
    guild = interaction.guild

    async with async_session() as session:
        resultado = await session.execute(
            select(Recrutamento).where(
                Recrutamento.discord_id_candidato == candidato.id,
                Recrutamento.status == "EM_PROVA",
            )
        )
        recrutamento = resultado.scalar_one()

        resultado_respostas = await session.execute(
            select(RespostaProva).where(RespostaProva.recrutamento_id == recrutamento.id)
        )
        respostas = resultado_respostas.scalars().all()

        acertos = sum(1 for r in respostas if r.correta)
        percentual = round((acertos / TOTAL_PERGUNTAS_PROVA) * 100, 1)

        recrutamento.acertos = acertos
        recrutamento.nota_percentual = percentual
        recrutamento.status = "AGUARDANDO_DECISAO"
        await session.commit()

        respostas_erradas_ids = [r.numero_pergunta for r in respostas if not r.correta]

        detalhes_erros = []
        for resposta in respostas:
            if resposta.correta:
                continue

            resultado_pergunta = await session.execute(
                select(Pergunta).where(Pergunta.ordem == resposta.numero_pergunta)
            )
            pergunta = resultado_pergunta.scalar_one()
            opcoes = json.loads(pergunta.opcoes)
            letras = ["A", "B", "C", "D"]

            texto_resposta_dada = opcoes[letras.index(resposta.resposta_escolhida)]
            texto_resposta_correta = opcoes[letras.index(pergunta.resposta_correta)]

            detalhes_erros.append({
                "numero": resposta.numero_pergunta,
                "enunciado": pergunta.enunciado,
                "resposta_dada": texto_resposta_dada,
                "resposta_correta": texto_resposta_correta,
            })

    await interaction.response.edit_message(view=AvaliacaoEnviadaView(acertos, percentual))


    canal = guild.get_channel(CANAIS["APROVAR_REPROVAR"])
    status_emoji = "✅ Apto para aprovação" if percentual >= NOTA_MINIMA_APROVACAO else "❌ Abaixo da nota mínima"
    cor = discord.Color.green() if percentual >= NOTA_MINIMA_APROVACAO else discord.Color.red()

    view_resultado = AprovacaoView(
        candidato=candidato, recrutamento_id=recrutamento.id,
        nota=percentual, acertos=acertos, total=TOTAL_PERGUNTAS_PROVA,
        respostas_erradas=respostas_erradas_ids, detalhes_erros=detalhes_erros,
        status_emoji=status_emoji, guild=guild, cor=cor,
    )
    mensagem = await canal.send(view=view_resultado)
    view_resultado.message = mensagem  # necessário para editar depois na aprovação/reprovação


class AvaliacaoEnviadaView(discord.ui.LayoutView):
    def __init__(self, acertos: int, percentual: float):
        super().__init__(timeout=None)
        container = discord.ui.Container(
            discord.ui.TextDisplay("# ✅ Avaliação enviada!"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                f"Você acertou **{acertos}/{TOTAL_PERGUNTAS_PROVA}** (`{percentual}%`).\n"
                f"Aguarde a decisão do recrutador."
            ),
            accent_color=discord.Color.blurple(),
        )
        self.add_item(container)