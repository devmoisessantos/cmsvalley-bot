# src/utils/views.py
"""
A caixinha de confirmacao que qualquer dominio pode reusar.

Para que serve
--------------
Algumas acoes nao tem volta: restaurar um backup, apagar registro, mexer em
cargo de todo mundo. Antes de fazer, o bot precisa perguntar "voce tem certeza?"
e esperar a pessoa clicar.

Esta view faz exatamente isso e nada mais. Quem chama fica esperando com
`await view.wait()` e depois olha `view.confirmado`:

    view_de_confirmacao = ViewDeConfirmacao(autor_id=interacao.user.id)
    await enviar_card(interacao, view_de_confirmacao)
    await view_de_confirmacao.wait()

    if view_de_confirmacao.confirmado is True:
        ...  # a pessoa clicou em Confirmar
    elif view_de_confirmacao.confirmado is False:
        ...  # a pessoa clicou em Cancelar
    else:
        ...  # ninguem clicou e o tempo acabou

Por que sao tres respostas e nao duas
-------------------------------------
`confirmado` comeca como None. Se ficasse False desde o inicio, o codigo nao
saberia diferenciar "a pessoa cancelou" de "a pessoa foi embora e o tempo
acabou" — e essas duas situacoes merecem mensagens diferentes.

A migracao para Components V2
-----------------------------
Este arquivo era o ultimo do projeto com `discord.ui.View` classico. A propria
docstring antiga dizia: "quando formos migrar confirmacoes para Components V2,
este arquivo sera o lugar". Chegou a hora.

Agora ela e uma `LayoutView` com `Container`, igual a todo o resto do bot. O
nome antigo `ConfirmView` continua funcionando como apelido, porque
`src/backup/backup_cogs.py` ja chama por esse nome — mandamento 10: nunca
quebrar o que funciona.
"""

from __future__ import annotations

import discord

from src.utils.mensagens import (
    editar_mensagem_original,
    responder_erro,
)

TEMPO_PADRAO_PARA_RESPONDER_EM_SEGUNDOS = 30

COR_DE_ATENCAO = discord.Color.from_str("#F1C40F")


class LinhaDeBotoesDeConfirmacao(discord.ui.ActionRow):
    """
    Os dois botoes da caixinha: Confirmar e Cancelar.

    Em Components V2 os botoes moram dentro de uma linha de acoes, e a linha
    mora dentro do container. Por isso eles ficam nesta classe separada e nao
    soltos na view.
    """

    def __init__(self, view_da_confirmacao: ViewDeConfirmacao) -> None:
        super().__init__()
        self.view_da_confirmacao = view_da_confirmacao

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.danger, emoji="✅")
    async def ao_confirmar(
        self,
        interacao: discord.Interaction,
        botao_de_confirmacao: discord.ui.Button,
    ) -> None:
        """
        Marca a escolha positiva, desliga os botoes e libera quem esperava.

        Desligar os botoes e importante: sem isso, um segundo clique executaria
        a acao sem volta duas vezes.
        """
        await self.view_da_confirmacao.registrar_escolha(
            interacao,
            escolheu_confirmar=True,
            texto_do_aviso="✅ Confirmado. Aplicando…",
        )

    @discord.ui.button(
        label="Cancelar", style=discord.ButtonStyle.secondary, emoji="❌"
    )
    async def ao_cancelar(
        self,
        interacao: discord.Interaction,
        botao_de_cancelamento: discord.ui.Button,
    ) -> None:
        """Marca a desistencia, desliga os botoes e libera quem esperava."""
        await self.view_da_confirmacao.registrar_escolha(
            interacao,
            escolheu_confirmar=False,
            texto_do_aviso="❌ Operacao cancelada.",
        )


class ViewDeConfirmacao(discord.ui.LayoutView):
    """
    Pergunta "tem certeza?" e espera o clique de quem pediu a acao.

    Guarda a resposta em `confirmado`: True para confirmado, False para
    cancelado e None quando o tempo acabou sem clique.

    So a pessoa que executou o comando consegue clicar. Sem essa trava,
    qualquer um que passasse pelo canal poderia confirmar a restauracao de
    backup de outra pessoa.
    """

    def __init__(
        self,
        autor_id: int,
        timeout: int = TEMPO_PADRAO_PARA_RESPONDER_EM_SEGUNDOS,
        titulo: str = "Confirmacao necessaria",
        pergunta: str = "Esta acao nao tem volta. Deseja continuar?",
    ) -> None:
        super().__init__(timeout=timeout)

        self.autor_id = autor_id
        self.confirmado: bool | None = None

        self.container_da_pergunta = discord.ui.Container(accent_colour=COR_DE_ATENCAO)
        self.container_da_pergunta.add_item(discord.ui.TextDisplay(f"## ⚠️ {titulo}"))
        self.container_da_pergunta.add_item(discord.ui.TextDisplay(pergunta))
        self.container_da_pergunta.add_item(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small)
        )

        self.linha_de_botoes = LinhaDeBotoesDeConfirmacao(self)
        self.container_da_pergunta.add_item(self.linha_de_botoes)

        self.add_item(self.container_da_pergunta)

    async def interaction_check(self, interacao: discord.Interaction) -> bool:
        """
        Deixa passar so a pessoa que iniciou a acao sem volta.

        Devolve False para qualquer outra pessoa, e o Discord entende que nao
        deve chamar a funcao do botao. Antes de recusar, avisa a pessoa com uma
        mensagem so ela ve, para ninguem ficar achando que o bot travou.
        """
        membro_que_clicou = interacao.user

        if membro_que_clicou.id != self.autor_id:
            await responder_erro(
                interacao,
                titulo="Acao nao permitida",
                linhas=["Apenas quem executou o comando pode confirmar."],
            )
            return False

        return True

    async def registrar_escolha(
        self,
        interacao: discord.Interaction,
        *,
        escolheu_confirmar: bool,
        texto_do_aviso: str,
    ) -> None:
        """
        Grava a escolha, desliga os botoes e para de esperar.

        Chamada pelos dois botoes. Ela edita a mensagem no Discord com o aviso
        do que aconteceu, para a pessoa ver que o clique foi recebido, e so
        depois chama `stop()` — que e o que solta o `await view.wait()` de quem
        estava esperando do outro lado.
        """
        self.confirmado = escolheu_confirmar

        self.desligar_os_botoes()
        self.container_da_pergunta.add_item(discord.ui.TextDisplay(texto_do_aviso))

        await editar_mensagem_original(interacao, view=self)
        self.stop()

    def desligar_os_botoes(self) -> None:
        """Impede um segundo clique depois de a escolha ja ter sido feita."""
        for botao_da_linha in self.linha_de_botoes.children:
            botao_da_linha.disabled = True

    @property
    def value(self) -> bool | None:
        """
        Apelido em ingles de `confirmado`, mantido para nao quebrar chamadas.

        O nome certo neste projeto e `confirmado`, em portugues. Este apelido
        existe porque codigo antigo lia `view.value`, e o mandamento 10 diz para
        nunca quebrar o que funciona. Em codigo novo, use `confirmado`.
        """
        return self.confirmado


# Nome antigo, mantido porque src/backup/backup_cogs.py ja importa assim.
ConfirmView = ViewDeConfirmacao
