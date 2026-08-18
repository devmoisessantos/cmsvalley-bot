# src/utils/decoradores.py
"""
Decoradores reutilizaveis para comandos, botoes e selects.

Por que este arquivo existe:

Antes, cada cog repetia a mesma checagem de cargo na mao e cada callback de
botao repetia o mesmo try/except. Isso significava a mesma regra escrita em
dezenas de lugares, cada um podendo esquecer um detalhe.

Aqui as regras ficam escritas uma unica vez. Se a regra mudar, muda so aqui.

Como usar:

    from src.utils.decoradores import (
        apenas_cargos,
        capturar_erros,
        somente_no_canal,
    )

    @app_commands.command(name="punir")
    @apenas_cargos(CARGOS_DIRETORIA)
    @capturar_erros("comando /punir")
    async def punir(self, interacao: discord.Interaction):
        ...
"""

from __future__ import annotations

import functools
import inspect

import discord
from discord import app_commands

from src.utils.error_handling import capturar_erro_e_logar
from src.utils.mensagens import (
    responder_aviso,
    responder_erro,
)
from src.utils.permissions import (
    membro_e_administrador,
    membro_tem_algum_cargo,
)

# ---------------------------------------------------------------------------
# Duas excecoes as regras gerais do projeto, documentadas de proposito
# ---------------------------------------------------------------------------
#
# 1. `*argumentos_recebidos` / `**argumentos_nomeados`
#
# O AGENTS.md proibe *args e **kwargs no codigo comum, e a proibicao esta
# certa: em uma funcao de dominio eles esconderam o que a funcao recebe.
# Um decorador, porem, nao sabe nem pode saber a assinatura da funcao que
# vai envolver: ele precisa aceitar qualquer coisa e repassar igual. A
# alternativa seria escrever um decorador diferente para cada assinatura,
# o que traria de volta exatamente a duplicacao que este arquivo elimina.
# Por isso *args/**kwargs aparecem AQUI e somente aqui, dentro dos
# decoradores, nunca em codigo de dominio.
#
# 2. Nota sobre o `functools` importado acima:
#
# O AGENTS.md proibe usar functools para fazer "codigo esperto" (partial,
# reduce, lru_cache espalhados pela logica). Aqui ele aparece apenas com
# `functools.wraps`, que existe para o contrario disso: preservar o nome e a
# docstring da funcao original para que o discord.py continue reconhecendo o
# comando e para que o erro no log mostre o nome certo. Sem ele, todos os
# comandos decorados apareceriam com o mesmo nome generico.


def apenas_cargos(nomes_dos_cargos_permitidos: list[str]):
    """
    Libera o comando somente para quem tem um dos cargos da lista.

    Administradores do servidor passam sempre.
    Quem nao tem permissao recebe um card de erro explicando o motivo.
    """

    async def verificar_cargos_do_membro(interacao: discord.Interaction) -> bool:
        """
        Decide se a interação pode chegar ao comando protegido pelo decorador.

        Esta verificação é reutilizada por todos os domínios: aceita administradores
        e cargos permitidos, responde no Discord quando não há autorização e retorna
        um booleano que impede a execução antes de qualquer efeito do comando.
        """
        membro_que_usou_o_comando = interacao.user

        if not isinstance(membro_que_usou_o_comando, discord.Member):
            await responder_erro(
                interacao,
                titulo="Comando indisponível aqui",
                linhas=["Este comando so funciona dentro do servidor."],
            )
            return False

        if membro_e_administrador(membro_que_usou_o_comando):
            return True

        if membro_tem_algum_cargo(
            membro_que_usou_o_comando,
            nomes_dos_cargos_permitidos,
        ):
            return True

        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=[
                "Voce nao tem nenhum dos cargos necessarios para usar este comando.",
            ],
        )
        return False

    return app_commands.check(verificar_cargos_do_membro)


def somente_no_canal(ids_dos_canais_permitidos: list[int]):
    """
    Libera o comando somente dentro dos canais informados.

    Serve para comandos que fazem sentido apenas em um canal especifico, como
    o de plantao ou o de chamada.
    """

    async def verificar_canal_da_interacao(interacao: discord.Interaction) -> bool:
        """
        Bloqueia cedo comandos usados fora dos canais definidos para sua operação.

        Como utilitário reutilizado por todos os domínios, também monta as menções
        permitidas e envia o aviso centralizado. O retorno booleano é consumido pelo
        Discord para que a função protegida nem seja chamada no canal errado.
        """
        canal_da_interacao = interacao.channel

        if canal_da_interacao is None:
            return False

        if canal_da_interacao.id in ids_dos_canais_permitidos:
            return True

        mencoes_dos_canais = []
        for id_do_canal_permitido in ids_dos_canais_permitidos:
            mencoes_dos_canais.append(f"<#{id_do_canal_permitido}>")

        await responder_aviso(
            interacao,
            titulo="Canal errado",
            linhas=[
                "Este comando so funciona nestes canais:",
                ", ".join(mencoes_dos_canais),
            ],
        )
        return False

    return app_commands.check(verificar_canal_da_interacao)


def capturar_erros(nome_do_contexto: str, avisar_o_membro: bool = True):
    """
    Envolve a funcao em um try/except que registra o erro no LOG_ERROS.

    Use em callbacks de botao, select e modal, e em comandos que fazem varias
    coisas. Assim nenhum erro passa em silencio, e o membro recebe um aviso
    amigavel em vez de ver "Esta interacao falhou".

    A funcao decorada precisa receber uma `discord.Interaction` em algum dos
    seus parametros (posicional ou nomeado) para que o log saiba quem clicou.
    """

    def aplicar_decorador(funcao_original):
        """
        Preserva a função original enquanto adiciona proteção compartilhada de erros.

        Existe dentro do decorador porque recebe a função concreta que será envolvida;
        devolve uma corrotina com seus metadados preservados, essencial para o
        discord.py continuar reconhecendo comandos e callbacks de todos os domínios.
        """

        @functools.wraps(funcao_original)
        async def funcao_protegida(*argumentos_recebidos, **argumentos_nomeados):
            """
            Executa o callback e transforma exceções em registro e resposta amigável.

            Procura a interação entre argumentos de assinatura desconhecida, pois este
            utilitário atende todos os domínios. Ao capturar uma falha, grava o erro e
            evita que o Discord mostre apenas uma interação sem resposta.
            """
            interacao_encontrada = _encontrar_interacao(
                argumentos_recebidos,
                argumentos_nomeados,
            )

            try:
                return await funcao_original(
                    *argumentos_recebidos,
                    **argumentos_nomeados,
                )
            except Exception as erro_capturado:
                await capturar_erro_e_logar(
                    erro_capturado,
                    contexto=nome_do_contexto,
                    interacao=interacao_encontrada,
                    avisar_o_membro=avisar_o_membro,
                )
                return None

        return funcao_protegida

    return aplicar_decorador


def _encontrar_interacao(
    argumentos_recebidos: tuple,
    argumentos_nomeados: dict,
) -> discord.Interaction | None:
    """
    Procura a interacao entre os argumentos recebidos pela funcao decorada.

    Precisa procurar porque a interacao pode chegar como primeiro argumento
    (funcao solta), como segundo (metodo de cog, depois do self) ou com nome
    (`interacao=` / `interaction=`).
    """
    for argumento in argumentos_recebidos:
        if isinstance(argumento, discord.Interaction):
            return argumento

    for valor_do_argumento in argumentos_nomeados.values():
        if isinstance(valor_do_argumento, discord.Interaction):
            return valor_do_argumento

    return None


def exige_ser_membro_do_servidor(funcao_original):
    """
    Garante que quem chamou o comando e um membro do servidor, nao uma DM.

    Muitas funcoes leem `interacao.user.roles`, que so existe em `Member`.
    Sem esta checagem, o comando quebraria com AttributeError na DM.
    """

    @functools.wraps(funcao_original)
    async def funcao_protegida(*argumentos_recebidos, **argumentos_nomeados):
        """
        Impede callbacks reutilizados de acessar cargos inexistentes em mensagens
        diretas.

        Localiza a interação sem conhecer a assinatura da função e confirma que o
        usuário é `Member` antes de delegar. Essa proteção comum a todos os domínios
        responde no Discord em vez de deixar um `AttributeError` interromper o fluxo.
        """
        interacao_encontrada = _encontrar_interacao(
            argumentos_recebidos,
            argumentos_nomeados,
        )

        if interacao_encontrada is None:
            return await funcao_original(*argumentos_recebidos, **argumentos_nomeados)

        if not isinstance(interacao_encontrada.user, discord.Member):
            await responder_erro(
                interacao_encontrada,
                titulo="Comando indisponível aqui",
                linhas=["Este comando so funciona dentro do servidor, nao na DM."],
            )
            return None

        return await funcao_original(*argumentos_recebidos, **argumentos_nomeados)

    return funcao_protegida


def _e_funcao_assincrona(funcao_para_checar) -> bool:
    """Diz se a funcao recebida e `async def`. Usado nos testes dos decoradores."""
    return inspect.iscoroutinefunction(funcao_para_checar)
