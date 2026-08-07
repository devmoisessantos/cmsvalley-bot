# src/guia/tutoriais_panel.py
"""
Painel de tutoriais — Guia do Estagiário (canal de tutoriais).

Components V2: LayoutView + Container + Section/Thumbnail + Select.
Cada opção do select abre um card efêmero (ephemeral=True, sem auto-exclusão).
"""

from __future__ import annotations

import discord

from src.config import GUIA_DE_TUTORIAIS
from src.guia.guia_helpers import (
    montar_linha_de_botoes_link,
    montar_thumbnail_do_servidor,
)
from src.utils.error_handling import LoggingViewMixin
from src.utils.mensagens import (
    responder_erro,
    responder_view,
)

# ---------------------------------------------------------------------------
# Conteúdo de cada tópico do select
# ---------------------------------------------------------------------------

# Cada tópico tem:
# - emoji / titulo / descricao (select)
# - blocos: lista de textos (cada um vira um TextDisplay)
# - botoes: lista de {rotulo, chave_do_canal} para botões de link

OPCOES_DOS_TUTORIAIS: dict[str, dict] = {
    "atendimento": {
        "emoji": "🚑",
        "titulo": "Atendimento dos Estagiários",
        "descricao": "Regras de atendimento e áreas de atuação",
        "blocos": [
            "# 🚑 Atendimento dos Estagiários\n"
            "**ATENÇÃO!**\n"
            "Estagiários **NÃO** realizam atendimentos fora do hospital.\n"
            "Os atendimentos externos são de responsabilidade "
            "**exclusiva dos Paramédicos**.\n"
            "Sempre respeite essa regra para evitar punições.",
            "## 📌 Progressão de Carreira — Áreas de Atuação (Paramédico)\n"
            "> Todo(a) paramédico(a) com **7 dias** dentro da organização "
            "deve **OBRIGATORIAMENTE** escolher uma área de atuação:",
            "**🎓 Instrutor(a)**\n"
            "`•` Requisito: Cursos Práticos 1.0 e 2.0\n"
            "`•` Cargo: <@&1522579028526239744>\n\n"
            "**📋 Recrutador(a)**\n"
            "`•` Requisito: Cursos Práticos 1.0\n"
            "`•` Cargo: <@&1522579072197197966>\n\n"
            "**🧠 Psicólogo(a)**\n"
            "`•` Requisito: Cursos Práticos 1.0\n"
            "`•` Cargo: <@&1486368771017805996>\n\n"
            "**🩺 Doutor(a)**\n"
            "`•` Requisito: Cursos Práticos 1.0\n"
            "`•` Cargo: <@&1486368771860856882>",
            "**Observações importantes:**\n"
            "`•` Enfermeiros trabalham **somente no atendimento interno**.\n"
            "`•` ❌ Não podem sair do HP uniformizados.",
        ],
        "botoes": [],
    },
    "cobranca": {
        "emoji": "🤑",
        "titulo": "Como Realizar uma Cobrança",
        "descricao": "Comando F8, valores e penalidades",
        "blocos": [
            "# 🤑 Como Realizar uma Cobrança\n"
            "## 📟 Comando no F8\n"
            "Utilize o seguinte comando no **F8**:\n"
            "```\ncobrar ID VALOR\n```\n"
            "### Exemplo prático:\n"
            "```\ncobrar 104972 8000\n```",
            "## ⚙️ Procedimento passo a passo\n"
            "Após digitar o comando:\n"
            "1. Pressione **ENTER**.\n"
            "2. Feche o **F8**.\n"
            "3. Pressione **Y** duas vezes para confirmar e enviar a cobrança.",
            "## 💰 Política de Valores\n"
            "> **É PROIBIDO cobrar qualquer valor acima do permitido.**\n\n"
            "### 📌 Valores Padrão — Atendimento no HP (Hospital)\n"
            "`•` ❤️ Tratamento — **R$ 8.000** (oito mil)\n"
            "`•` 💀 Reanimação — **R$ 8.000** (oito mil)\n\n"
            "### 📌 Valor Padrão — Atendimento Externo\n"
            "`•` 💀 Reanimação — **R$ 16.000** (dezesseis mil)",
            "## ⚠️ Penalidades e Exceções\n"
            "`•` **Cobrança acima do valor padrão** = reembolso obrigatório.\n"
            "`•` **Cobrança acima do valor padrão só é permitida** com "
            "autorização **explícita** do paciente.\n"
            "`•` O descumprimento destas regras está sujeito a sanções disciplinares.",
        ],
        "botoes": [],
    },
    "binds": {
        "emoji": "⌨️",
        "titulo": "Binds Médicas",
        "descricao": "Teclas padrão, configuração e proibições",
        "blocos": [
            "# ⌨️ Binds Médicas\n"
            "## 🎮 Teclas Padrão e Funções\n"
            "`•` **1** — ID/Passaporte: puxa o ID do FiveM da pessoa\n"
            "`•` **2** — Tratamento: entrega tratamento (**somente na maca**)\n"
            "`•` **3** — Reanimar: reanima o jogador desmaiado\n"
            "`•` **0** — Uniforme: veste o uniforme do hospital\n"
            "`•` **F7** — Toggle: entra / sai de serviço na cidade",
            "## ⚙️ Configuração e Personalização\n"
            "`•` As binds já vêm **pré-definidas** no uniforme do CMS Valley.\n"
            "`•` Consulte o canal do seu uniforme para detalhes.\n\n"
            "### ✅ Permitido\n"
            "`•` Alterar as teclas das binds conforme preferência pessoal.\n\n"
            "### ❌ Proibido\n"
            "`•` **Remover** qualquer bind essencial.\n"
            'Exemplo: `unbind keyboard "3";`\n'
            "Isso remove a bind de reanimar e **impossibilita** reanimar pacientes.",
            "> ⚠️ **Aviso:** A remoção de binds médicas é considerada conduta "
            "inadequada e poderá acarretar medidas disciplinares.",
        ],
        "botoes": [
            {
                "rotulo": "Canal de Uniformes",
                "chave_do_canal": "GUIA_UNIFORME",
            },
        ],
    },
    "radio": {
        "emoji": "📻",
        "titulo": "Rádio e Comunicação",
        "descricao": "Frequências, calls e regras de uso",
        "blocos": [
            "# 📻 Rádio e Comunicação\n"
            "## 🔊 Obrigatoriedade\n"
            "📌 O uso da rádio durante o serviço é **OBRIGATÓRIO** para todos os membros.",
            "## 📻 Canais de Rádio (In-Game)\n"
            "`•` **Canal 1 — Interno** — Frequência **12** — Assuntos internos do hospital\n"
            "`•` **Canal 2 — Externo** — Frequência **13** — Atendimentos e comunicação externa",
            "## 🎧 Canais de Voz (Discord)\n"
            "É importante conectar-se às calls ao iniciar o serviço:\n"
            "`•` ┃🔇・INTERNA 12 — Assuntos do hospital\n"
            "`•` ┃🔇・EXTERNA 13 — Atendimentos externos",
            "## 📋 Regras de Uso\n"
            "### Rádio Interna (12)\n"
            "`•` Destinada **somente** para assuntos do hospital.\n"
            "`•` ❌ **Evite conversas paralelas.**\n"
            "`•` Microfone mutado é **permitido** durante atendimento interno.\n\n"
            "### Obrigatoriedade\n"
            "`•` É obrigatório o uso da rádio **IN-GAME** enquanto estiver com toggle **ativo**.",
            "## ⚠️ Penalidade\n"
            "> O descumprimento das regras de rádio e comunicação acarreta: **ADV1**",
            "## 🤝 Benefícios da Call no Discord\n"
            "A participação na call do Discord **não é obrigatória**, porém é "
            "**altamente recomendada**, pois:\n"
            "`•` Facilita a comunicação entre a equipe\n"
            "`•` Agiliza solicitações de ajuda (**UP**)\n"
            "`•` Permite contato rápido com outros membros\n"
            "`•` Você participa dos **sorteios** realizados pela equipe\n"
            "`•` Demonstra maior **comprometimento** e presença na corporação\n\n"
            "> 💡 **A boa comunicação faz toda a diferença no atendimento e no trabalho em equipe.**",
            "## 🚫 Conduta\n"
            "### Art. 20\n"
            "> **PROIBIDO** aceitar propina, seja dinheiro ou itens.",
        ],
        "botoes": [
            {
                "rotulo": "Call INTERNA 12",
                "chave_do_canal": "CALL_INTERNA_12",
            },
            {
                "rotulo": "Call EXTERNA 13",
                "chave_do_canal": "CALL_EXTERNA_13",
            },
        ],
    },
    "toggle": {
        "emoji": "🏥",
        "titulo": "Entrar ou Sair de Serviço",
        "descricao": "Toggle, regras e plantão no Discord",
        "blocos": [
            "# 🏥 Entrar ou Sair de Serviço (Toggle)\n"
            "## 🎮 Comandos e Teclas\n"
            "`•` Comando no **F8**: `toggle`\n"
            "`•` Tecla de atalho: **F7**",
            "## ⚠️ Regras Importantes\n"
            "### 🚫 Proibições\n"
            "`•` É **PROIBIDO** usar o toggle para **evitar ser saqueado**.\n"
            "`•` É **PROIBIDO** permanecer com toggle ligado ao **sair da cidade**.\n\n"
            "### ✅ Obrigações\n"
            "`•` Sempre que sair da cidade, **desligar o toggle** pelo F7 (caso esteja ligado).\n"
            "`•` Ao iniciar serviço na cidade, **iniciar também o plantão no Discord**.",
            "## 💰 Sistema de Recompensa — Plantão no Discord\n"
            "Ao entrar em serviço no Discord, você acumula benefícios:\n"
            "`•` Recebe **salário na cidade**.\n"
            "`•` É recompensado por cada **30 minutos** de permanência na call enquanto em serviço.",
            "> 📌 **Resumo:** Sempre que entrar em serviço na cidade, inicie seu "
            "plantão no canal do Discord para acumular tempo e garantir sua recompensa.",
        ],
        "botoes": [
            {
                "rotulo": "Iniciar Plantão",
                "chave_do_canal": "CANAL_PAINEL_PLANTAO_ID",
            },
        ],
    },
    "paramedico": {
        "emoji": "📈",
        "titulo": "Como se Tornar Paramédico",
        "descricao": "Progressão Estagiário → Enfermeiro → Paramédico",
        "blocos": [
            "# 📈 Como se Tornar Paramédico\n"
            "## 🎯 Visão Geral\n"
            "Para se tornar paramédico no **Centro Médico Sul Valley**, é necessário "
            "cumprir uma série de requisitos progressivos.",
            "## 🟢 Estagiário → Enfermeiro\n"
            "**Cargo:** <@&1486368782585954405> → <@&1522567683269333012>\n\n"
            "### 📌 Requisitos Obrigatórios\n"
            "`•` ⏱️ **Horas em serviço ativo:** 2 horas completas **dentro do hospital** "
            "(atendendo, auxiliando ou exercendo função — **não conta AFK**)\n"
            "`•` 🎧 **Horas na call interna:** 2 horas completas na call INTERNA 12\n"
            "`•` ✅ **Conduta:** boa conduta e respeito à hierarquia\n"
            "`•` ❌ **Advertências:** não possuir advertência ativa",
            "## 🔴 Enfermeiro → Paramédico\n"
            "**Cargo:** <@&1522567683269333012>\n\n"
            "### 📌 Requisitos Obrigatórios\n"
            "`•` ⏱️ **Horas em serviço ativo:** mínimo de 2 horas **dentro do hospital** "
            "(atendendo ou exercendo função — **não conta AFK**)\n"
            "`•` 🎧 **Horas na call interna:** mínimo de 2 horas na call INTERNA 12",
            "## 🎓 Curso de Resgate\n"
            "> **OBRIGATÓRIO** para progressão a Paramédico.\n\n"
            "### 💰 Formas de Aquisição\n"
            "`•` 💵 **Pago:** R$ 200.000 (duzentos mil)\n"
            "`•` 🆓 **Gratuito:** após cumprir **2 horas de serviço ativo** dentro do hospital",
            "## 🔓 Liberação de Atendimento Externo\n"
            "Após cumprir **todos os requisitos** e concluir o **Curso de Resgate**:\n"
            "`•` ✅ **Atendimento externo autorizado**\n"
            "`•` 🚑 Pode atuar **fora do hospital**",
        ],
        "botoes": [
            {
                "rotulo": "Call INTERNA 12",
                "chave_do_canal": "CALL_INTERNA_12",
            },
        ],
    },
    "curso_resgate": {
        "emoji": "🚨",
        "titulo": "Curso de Resgate",
        "descricao": "Fluxo completo: solicitação até promoção",
        "blocos": [
            "# 🚨 Curso de Resgate\n"
            "## 📋 Visão Geral\n"
            "Para atuar como **Paramédico**, é **obrigatório** realizar o **Curso de Resgate**.",
            "## 🔄 Fluxo Completo do Processo\n"
            "### 1️⃣ Solicitação do Curso\n"
            "Solicite seu curso no canal dedicado (botão abaixo).\n\n"
            "### 2️⃣ Aguardar Instrutor\n"
            "Após solicitar, entre **imediatamente** no canal de voz "
            "**Aguardando Curso** e aguarde um instrutor.\n"
            "> ⏳ Aguarde sua vez com **paciência** e mantenha-se **disponível**.",
            "### 3️⃣ Material do Curso\n"
            "Após cumprir os requisitos e ter o curso aceito, baixe o "
            "**Arquivo do Curso de Resgate** no canal de material.\n\n"
            "### 4️⃣ Instrução Prática\n"
            "`•` Após a leitura do material, o **instrutor** fornecerá "
            "instruções adicionais e esclarecerá dúvidas.\n\n"
            "### 5️⃣ Solicitação de Promoção\n"
            "Após conclusão bem-sucedida, solicite a promoção no canal dedicado.",
            "## ✅ Resumo do Processo\n"
            "`•` 1 — Cumprir requisitos de horas (serviço ativo + call interna)\n"
            "`•` 2 — Solicitar o Curso de Resgate\n"
            "`•` 3 — Entrar na call **Aguardando Curso**\n"
            "`•` 4 — Baixar e ler o arquivo do treinamento\n"
            "`•` 5 — Realizar o curso com o instrutor\n"
            "`•` 6 — Solicitar promoção para Paramédico",
            "> ⚠️ **Importante:** Após receber sua promoção no Discord, "
            "lembre-se de solicitar também a promoção **dentro do jogo (in-game)**.",
        ],
        "botoes": [
            {
                "rotulo": "Solicitar Curso",
                "chave_do_canal": "SOLICITAR_CURSO_RESGATE",
            },
            {
                "rotulo": "Call Aguardando Curso",
                "chave_do_canal": "CALL_AGUARDANDO_CURSO",
            },
            {
                "rotulo": "Material do Curso",
                "chave_do_canal": "MATERIAL_CURSO_RESGATE",
            },
            {
                "rotulo": "Solicitar Promoção",
                "chave_do_canal": "SOLICITAR_PROMOCAO_PARAMEDICO",
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# Montagem do card efêmero
# ---------------------------------------------------------------------------


def montar_card_do_tutorial(chave_do_topico: str) -> discord.ui.LayoutView:
    """
    Monta o card efêmero (Components V2) do tópico escolhido no select.

    A mensagem não some sozinha (o membro pode reler com calma).
    """
    dados_do_topico = OPCOES_DOS_TUTORIAIS[chave_do_topico]

    componentes: list = []

    for indice, bloco_de_texto in enumerate(dados_do_topico["blocos"]):
        if indice > 0:
            componentes.append(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
            )
        componentes.append(discord.ui.TextDisplay(bloco_de_texto))

    linha_dos_botoes = montar_linha_de_botoes_link(dados_do_topico.get("botoes") or [])
    if linha_dos_botoes is not None:
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(linha_dos_botoes)

    container = discord.ui.Container(
        *componentes,
        accent_color=discord.Color.blurple(),
    )
    view_do_card = discord.ui.LayoutView(timeout=None)
    view_do_card.add_item(container)
    return view_do_card


# ---------------------------------------------------------------------------
# Painel persistente
# ---------------------------------------------------------------------------


class PainelTutoriaisLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel persistente — tutoriais e procedimentos do CMS Valley."""

    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

        thumbnail_do_servidor = montar_thumbnail_do_servidor(guild)

        opcoes_do_select = [
            discord.SelectOption(
                label=dados["titulo"][:100],
                value=chave,
                description=dados["descricao"][:100],
                emoji=dados["emoji"],
            )
            for chave, dados in OPCOES_DOS_TUTORIAIS.items()
        ]

        select_dos_tutoriais = discord.ui.Select(
            placeholder="📖 Escolha um tutorial…",
            custom_id="guia:tutoriais_select",
            options=opcoes_do_select,
        )
        select_dos_tutoriais.callback = self._ao_selecionar_topico

        linha_do_select = discord.ui.ActionRow()
        linha_do_select.add_item(select_dos_tutoriais)

        componentes: list = [
            discord.ui.Section(
                "# 📖 Centro Médico Sul | Tutoriais",
                "> 📚 Guia prático — procedimentos, binds, cobrança e progressão.",
                accessory=thumbnail_do_servidor,
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                "Aqui você encontra os **tutoriais essenciais** do dia a dia "
                "no CMS Valley.\n\n"
                "Escolha um tópico no menu abaixo. O card abre **só para você** "
                "(mensagem privada no canal) e permanece até você fechar.\n\n"
                "Use sempre que precisar relembrar um procedimento."
            ),
        ]

        urls_da_galeria = [url for url in GUIA_DE_TUTORIAIS if url]
        galeria_tem_imagens = len(urls_da_galeria) > 0
        if galeria_tem_imagens:
            componentes.append(
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
            )
            componentes.append(
                discord.ui.MediaGallery(
                    *[discord.MediaGalleryItem(url) for url in urls_da_galeria[:10]]
                )
            )

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(discord.ui.TextDisplay("**👉 Selecione um tutorial: ↓**"))
        componentes.append(linha_do_select)

        self.container = discord.ui.Container(
            *componentes,
            accent_color=discord.Color.blurple(),
        )
        self.add_item(self.container)

    async def _ao_selecionar_topico(self, interacao: discord.Interaction):
        chave_escolhida = interacao.data["values"][0]

        topico_existe = chave_escolhida in OPCOES_DOS_TUTORIAIS
        if not topico_existe:
            await responder_erro(
                interacao,
                titulo="Opção inválida",
                linhas=["Esse tutorial não existe."],
            )
            return

        # ephemeral=True e sem auto-exclusão (timeout=None no card)
        view_do_card = montar_card_do_tutorial(chave_escolhida)
        await responder_view(interacao, view_do_card, ephemeral=True)
