"""
O questionario de avaliacao, pergunta por pergunta.

O que acontece aqui
-------------------
1. `iniciar_avaliacao` abre a prova para quem clicou no painel.
2. `montar_view_pergunta` desenha uma pergunta por vez, com barra de progresso.
3. `finalizar_avaliacao` soma os acertos, grava no banco e decide aprovado ou
   reprovado usando NOTA_MINIMA_APROVACAO de src/config.py.

Cuidado
-------
As respostas ficam gravadas na tabela RespostaProva enquanto a prova acontece,
nao so no final. Assim, se a pessoa cair da internet no meio, o que ela ja
respondeu nao se perde.
"""

import json
from datetime import datetime, timezone

import discord
from sqlalchemy import select

from src.config import CANAIS, NOTA_MINIMA_APROVACAO, TOTAL_PERGUNTAS_PROVA
from src.database.conexao import async_session
from src.database.models import Pergunta, Recrutamento, RespostaProva
from src.recrutamento.aprovacao_panel import AprovacaoView
from src.utils.error_handling import LoggingViewMixin
from src.utils.mensagens import (
    editar_mensagem_original,
    responder_erro,
    responder_view,
)


def barra_progresso(atual: int, total: int) -> str:
    """Monta a barra visual com a posição atual e o total de perguntas."""
    preenchido = "▰" * atual
    vazio = "▱" * (total - atual)
    return f"{preenchido}{vazio}  `{atual}/{total}`"


async def iniciar_avaliacao(interaction: discord.Interaction):
    """Reserva a única tentativa do candidato antes de mostrar a primeira pergunta.

    Confere se existe um recrutamento liberado e grava no banco que o formulário foi
    aberto, com horário de início e status de prova. Essa ordem impede que o mesmo
    candidato reabra a avaliação e envie respostas duplicadas em tentativas futuras.
    """
    candidato = interaction.user
    guild = interaction.guild

    await interaction.response.defer(ephemeral=True)

    async with async_session() as session:
        resultado = await session.execute(
            select(Recrutamento).where(
                Recrutamento.discord_id_candidato == candidato.id,
                Recrutamento.status == "PROVA_LIBERADA",
            )
        )
        recrutamento = (
            resultado.scalar_one_or_none()
        )  # 👈 troca .scalars().all() por isso

        if recrutamento is None:  # 👈 None, não "is None" numa lista
            await responder_erro(
                interaction,
                titulo="Sem recrutamento ativo",
                linhas=[
                    "Você não possui um recrutamento ativo em fase de estudo.",
                ],
            )
            return

        if recrutamento.formulario_aberto:
            await responder_erro(
                interaction,
                titulo="Avaliação já iniciada",
                linhas=[
                    "Sua avaliação já foi iniciada anteriormente e não pode ser "
                    "reaberta. "
                    "Caso tenha ocorrido um erro, procure um recrutador.",
                ],
            )
            return

        recrutamento.formulario_aberto = True
        recrutamento.status = "EM_PROVA"
        recrutamento.pergunta_atual = 0
        recrutamento.data_inicio_prova = datetime.now(timezone.utc)
        await session.commit()

    # 👇 NÃO troca mais cargo aqui — já foi trocado na liberação
    view = await montar_view_pergunta(numero=1, guild=guild)
    await responder_view(
        interaction,
        view,
        ephemeral=True,
    )


async def montar_view_pergunta(
    numero: int, guild: discord.Guild
) -> "PerguntaLayoutView":
    """Busca a pergunta pela ordem e entrega uma tela de resposta pronta.

    O número identifica a pergunta persistida no banco, enquanto a guilda fornece o
    ícone exibido ao candidato. A view retornada mantém esses dados para gravar a
    resposta correta no recrutamento certo quando o menu for usado.
    """
    async with async_session() as session:
        resultado = await session.execute(
            select(Pergunta).where(Pergunta.ordem == numero)
        )
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
                discord.SelectOption(
                    label=f"{letras[indice]}) {texto}", value=letras[indice]
                )
                for indice, texto in enumerate(opcoes)
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
            accessory=discord.ui.Thumbnail(logo_url)
            if logo_url
            else discord.ui.Thumbnail("attachment://logo.png"),
        )
        container = discord.ui.Container(
            cabecalho,
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(f"# 📝 Pergunta {numero}/{TOTAL_PERGUNTAS_PROVA}"),
            discord.ui.TextDisplay(
                f"-# {barra_progresso(numero, TOTAL_PERGUNTAS_PROVA)}"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(pergunta.enunciado),
            action_row,
            accent_color=discord.Color.gold(),
        )
        self.add_item(container)

    async def responder(self, interaction: discord.Interaction):
        """Grava a escolha antes de avançar o candidato para a próxima etapa.

        Salva no banco a alternativa, se ela está correta e a posição alcançada no
        recrutamento. Assim, uma troca de tela só acontece após preservar a resposta,
        evitando perder respostas quando o candidato conclui a prova.
        """
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

            session.add(
                RespostaProva(
                    recrutamento_id=recrutamento.id,
                    numero_pergunta=self.numero,
                    resposta_escolhida=resposta,
                    correta=correta,
                )
            )
            recrutamento.pergunta_atual = self.numero
            await session.commit()

        if self.numero >= TOTAL_PERGUNTAS_PROVA:
            await editar_mensagem_original(
                interaction,
                view=EnviarQuestionarioView(),
            )
        else:
            proxima_view = await montar_view_pergunta(
                numero=self.numero + 1, guild=interaction.guild
            )
            await editar_mensagem_original(
                interaction,
                view=proxima_view,
            )


class EnviarQuestionarioView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        self.botao = discord.ui.Button(
            label="Enviar Questionário", style=discord.ButtonStyle.success
        )
        self.botao.callback = self.enviar

        action_row = discord.ui.ActionRow()
        action_row.add_item(self.botao)

        container = discord.ui.Container(
            discord.ui.TextDisplay("# ✅ Questionário concluído"),
            discord.ui.TextDisplay(
                f"-# {barra_progresso(TOTAL_PERGUNTAS_PROVA, TOTAL_PERGUNTAS_PROVA)}"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                "Você respondeu todas as perguntas. Clique abaixo para enviar sua "
                "avaliação."
            ),
            action_row,
            accent_color=discord.Color.gold(),
        )
        self.add_item(container)

    async def enviar(self, interaction: discord.Interaction):
        """
        Encaminha a entrega final ao serviço que calcula a nota e notifica a equipe.
        """
        await finalizar_avaliacao(interaction)


async def finalizar_avaliacao(interaction: discord.Interaction):
    """Fecha a prova, grava a nota e envia à equipe os dados para a decisão.

    Calcula os acertos a partir das respostas persistidas e muda o recrutamento para
    aguardar decisão no banco. Também reúne os erros e publica no Discord um painel
    para os recrutadores, evitando que a decisão seja feita sem o resultado da prova.
    """
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
            select(RespostaProva).where(
                RespostaProva.recrutamento_id == recrutamento.id
            )
        )
        respostas = resultado_respostas.scalars().all()

        acertos = sum(1 for resposta_dada in respostas if resposta_dada.correta)
        percentual = round((acertos / TOTAL_PERGUNTAS_PROVA) * 100, 1)

        recrutamento.acertos = acertos
        recrutamento.nota_percentual = percentual
        recrutamento.status = "AGUARDANDO_DECISAO"
        await session.commit()

        respostas_erradas_ids = [
            resposta_dada.numero_pergunta
            for resposta_dada in respostas
            if not resposta_dada.correta
        ]

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

            detalhes_erros.append(
                {
                    "numero": resposta.numero_pergunta,
                    "enunciado": pergunta.enunciado,
                    "resposta_dada": texto_resposta_dada,
                    "resposta_correta": texto_resposta_correta,
                }
            )

    await editar_mensagem_original(
        interaction,
        view=AvaliacaoEnviadaView(acertos, percentual),
    )

    canal = guild.get_channel(CANAIS["APROVAR_REPROVAR"])
    status_emoji = (
        "✅ Apto para aprovação"
        if percentual >= NOTA_MINIMA_APROVACAO
        else "❌ Abaixo da nota mínima"
    )
    cor = (
        discord.Color.green()
        if percentual >= NOTA_MINIMA_APROVACAO
        else discord.Color.red()
    )

    view_resultado = AprovacaoView(
        candidato=candidato,
        recrutamento_id=recrutamento.id,
        nota=percentual,
        acertos=acertos,
        total=TOTAL_PERGUNTAS_PROVA,
        respostas_erradas=respostas_erradas_ids,
        detalhes_erros=detalhes_erros,
        status_emoji=status_emoji,
        guild=guild,
        cor=cor,
    )
    mensagem = await canal.send(view=view_resultado)
    view_resultado.message = (
        mensagem  # necessário para editar depois na aprovação/reprovação
    )


class AvaliacaoEnviadaView(discord.ui.LayoutView):
    def __init__(self, acertos: int, percentual: float):
        super().__init__(timeout=None)
        container = discord.ui.Container(
            discord.ui.TextDisplay("# ✅ Avaliação enviada!"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                f"Você acertou **{acertos}/{TOTAL_PERGUNTAS_PROVA}** "
                f"(`{percentual}%`).\n"
                f"Aguarde a decisão do recrutador."
            ),
            accent_color=discord.Color.blurple(),
        )
        self.add_item(container)
