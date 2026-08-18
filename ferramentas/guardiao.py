# ferramentas/guardiao.py
"""
O guardiao das regras do AGENTS.md.

Por que este arquivo existe
---------------------------
As regras do AGENTS.md sao boas, mas regra escrita em documento envelhece: seis
meses depois alguem cria um `discord.Embed` sem querer e ninguem percebe. Este
programa le o codigo e reclama sozinho.

Ele nao substitui o ruff. O ruff cuida de estilo (linha longa, import fora de
ordem, variavel que ninguem usa). O guardiao cuida das regras que so existem
neste projeto:

- nada de `discord.Embed`
- nada de `discord.ui.View` classico (aqui e Components V2)
- nada de resposta direta ao usuario fora de `src/utils/mensagens.py`
- nada de `except` que engole o erro em silencio
- nada de `print()` solto
- nada de nome de uma letra ou abreviacao
- nada de walrus, functools, itertools, map/filter/reduce, global, eval/exec
- nada de ID de canal ou cargo escrito no meio do codigo
- nada de arquivo fora do padrao `{dominio}_{tipo}.py`

Como usar
---------
    python ferramentas/guardiao.py

Sai com codigo 0 quando esta tudo certo e 1 quando achou problema — e assim que
o CI (ou voce, antes do commit) sabe que quebrou alguma regra.

Para ver so uma regra:

    python ferramentas/guardiao.py --regra embed

As excecoes combinadas
----------------------
Algumas coisas parecem violacao e nao sao. Elas estao em EXCECOES, cada uma com
o motivo escrito. Se voce precisar acrescentar uma excecao, escreva o motivo
junto: excecao sem motivo e so uma regra sendo furada.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
from dataclasses import dataclass, field

PASTA_DO_PROJETO = pathlib.Path(__file__).resolve().parent.parent
PASTA_FONTE = PASTA_DO_PROJETO / "src"

LARGURA_MAXIMA_DE_LINHA = 88

LIMITE_DE_LINHAS_POR_FUNCAO = 60


# ---------------------------------------------------------------------------
# As excecoes combinadas, cada uma com o motivo
# ---------------------------------------------------------------------------

# Estes arquivos podem responder direto ao usuario porque ELES SAO a camada de
# resposta. Se eles tivessem que chamar a camada de resposta, seria um circulo.
ARQUIVOS_QUE_PODEM_RESPONDER_DIRETO = {
    "src/utils/mensagens.py",
    "src/utils/log_container.py",
    "src/utils/error_handling.py",
}

# View classica que existe SO para o bot reconhecer o clique em mensagens
# antigas, postadas antes da migracao para Components V2. Se ela virasse
# LayoutView, os botoes daquelas mensagens paravam de funcionar para sempre.
# Nao e codigo novo: e compatibilidade com o passado.
ARQUIVOS_COM_VIEW_CLASSICA_JUSTIFICADA = {
    "src/financas/financas_views.py",
}

# O backup manda arquivo anexado, e anexo exige send_message com file=.
# A camada de mensagens nao cobre anexo porque nenhum outro dominio precisa.
ARQUIVOS_COM_RESPOSTA_DIRETA_JUSTIFICADA = {
    "src/backup/backup_cogs.py",
}

# Este arquivo escreve com cor no terminal durante o boot, quando ainda nao ha
# canal de log no Discord para receber nada. Aqui print() e a ferramenta certa.
ARQUIVOS_QUE_PODEM_USAR_PRINT = {
    "src/utils/deploy_logger.py",
}

# Termos da API do Discord e do SQLAlchemy. Sao contrato de biblioteca: mudar o
# nome quebraria a integracao. Ficam em ingles por obrigacao, nao por escolha.
NOMES_DA_API_EXTERNA = {
    "callback",
    "custom_id",
    "guild_id",
    "channel_id",
    "message_id",
    "user_id",
    "role_id",
    "avatar_url",
    "min_values",
    "max_values",
    "placeholder",
    "ephemeral",
    "setup",
    "cog_load",
    "cog_unload",
    "on_submit",
    "on_error",
    "on_timeout",
    "interaction_check",
    "self",
    "cls",
}

# Arquivos que nao seguem o padrao {dominio}_{tipo}.py e podem continuar assim.
ARQUIVOS_FORA_DO_PADRAO_ACEITOS = {
    "src/bot.py",
    "src/config.py",
    "src/__init__.py",
}

# Sufixos que descrevem papeis alem dos quatro principais. Cada um existe
# porque um dominio real precisou dele.
SUFIXOS_EXTRA = (
    "_permissoes",
    "_helpers",
    "_ocr",
)

PASTAS_QUE_NAO_SAO_DOMINIO = {"utils", "database", "data", "events", "__pycache__"}

SUFIXOS_DE_ARQUIVO_ACEITOS = (
    "_panel",
    "_service",
    "_cogs",
    "_logger",
    "_views",
    "_tasks",
    "_listener",
    "_modals",
    "_setup",
    "_state",
    "_helpers",
    "_classes",
    "_class",
    "_builder",
    "_modelo",
    "_paineis",
    "_api",
) + SUFIXOS_EXTRA


# ---------------------------------------------------------------------------
# Estrutura de uma reclamacao
# ---------------------------------------------------------------------------


@dataclass
class Reclamacao:
    """Um problema encontrado, com endereco e explicacao."""

    arquivo: str
    linha: int
    regra: str
    explicacao: str


@dataclass
class Relatorio:
    """Tudo o que o guardiao encontrou, agrupado por regra."""

    reclamacoes: list[Reclamacao] = field(default_factory=list)

    def anotar(self, arquivo: str, linha: int, regra: str, explicacao: str) -> None:
        """Guarda uma reclamacao nova."""
        self.reclamacoes.append(Reclamacao(arquivo, linha, regra, explicacao))

    def contar_por_regra(self) -> dict[str, int]:
        """Quantas reclamacoes de cada regra."""
        contagem: dict[str, int] = {}
        for reclamacao in self.reclamacoes:
            contagem[reclamacao.regra] = contagem.get(reclamacao.regra, 0) + 1
        return contagem


# ---------------------------------------------------------------------------
# As regras, uma funcao para cada
# ---------------------------------------------------------------------------


def conferir_embed(caminho: str, codigo: str, relatorio: Relatorio) -> None:
    """Regra: aqui e Components V2, ninguem cria discord.Embed."""
    for numero, linha in enumerate(codigo.split("\n"), 1):
        if linha.lstrip().startswith("#"):
            continue
        if "discord.Embed(" in linha or "Embed(" in linha and "discord" in linha:
            relatorio.anotar(
                caminho,
                numero,
                "embed",
                "discord.Embed e proibido. Use LayoutView com Container "
                "(Components V2), como em src/utils/mensagens.py.",
            )


def conferir_view_classico(caminho: str, codigo: str, relatorio: Relatorio) -> None:
    """Regra: painel novo nasce LayoutView, nao discord.ui.View."""
    if caminho in ARQUIVOS_COM_VIEW_CLASSICA_JUSTIFICADA:
        return

    for numero, linha in enumerate(codigo.split("\n"), 1):
        if linha.lstrip().startswith("#"):
            continue
        # Pega tambem quando a View aparece junto de um mixin, como em
        # class X(LoggingViewMixin, discord.ui.View). A versao antiga desta
        # regra so via a View sozinha entre parenteses e deixava esses passar.
        if re.search(r"class \w+\([^)]*\bdiscord\.ui\.View\b", linha):
            relatorio.anotar(
                caminho,
                numero,
                "view-classico",
                "View classico e proibido em codigo novo. Use "
                "discord.ui.LayoutView com Container.",
            )


def conferir_resposta_direta(caminho: str, codigo: str, relatorio: Relatorio) -> None:
    """Regra: quem fala com o usuario e src/utils/mensagens.py."""
    if caminho in ARQUIVOS_QUE_PODEM_RESPONDER_DIRETO:
        return
    if caminho in ARQUIVOS_COM_RESPOSTA_DIRETA_JUSTIFICADA:
        return

    padrao_de_resposta = re.compile(
        r"(interacao|interaction|interacao_do_usuario)\.response\.send_message\("
    )

    for numero, linha in enumerate(codigo.split("\n"), 1):
        if linha.lstrip().startswith("#"):
            continue
        if padrao_de_resposta.search(linha):
            relatorio.anotar(
                caminho,
                numero,
                "resposta-direta",
                "Resposta direta ao usuario. Use responder_sucesso, "
                "responder_erro, responder_aviso ou responder_info de "
                "src/utils/mensagens.py.",
            )


def conferir_except_silencioso(
    caminho: str, arvore: ast.Module, relatorio: Relatorio
) -> None:
    """Regra: erro engolido em silencio e bug escondido."""
    for no in ast.walk(arvore):
        if not isinstance(no, ast.ExceptHandler):
            continue

        if no.type is None:
            relatorio.anotar(
                caminho,
                no.lineno,
                "except-pelado",
                "except sem tipo captura ate KeyboardInterrupt. Diga qual "
                "erro voce espera.",
            )

        corpo_e_so_um_pass = len(no.body) == 1 and isinstance(no.body[0], ast.Pass)
        if corpo_e_so_um_pass:
            relatorio.anotar(
                caminho,
                no.lineno,
                "except-silencioso",
                "except com pass engole o erro. Registre com o registrador "
                "ou use ignorar_falha_cosmetica de src/utils/error_handling.py.",
            )


def conferir_print(caminho: str, arvore: ast.Module, relatorio: Relatorio) -> None:
    """Regra: print() nao tem nivel, nao tem hora e nao tem origem."""
    if caminho in ARQUIVOS_QUE_PODEM_USAR_PRINT:
        return

    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        if not isinstance(no.func, ast.Name):
            continue
        if no.func.id != "print":
            continue

        relatorio.anotar(
            caminho,
            no.lineno,
            "print",
            "print() solto. Use registrador.info / warning / error, com "
            "registrador = logging.getLogger(__name__) no topo do arquivo.",
        )


def conferir_ferramenta_proibida(
    caminho: str, arvore: ast.Module, relatorio: Relatorio
) -> None:
    """Regra: nada de codigo esperto que uma crianca nao leria."""
    for no in ast.walk(arvore):
        if isinstance(no, ast.NamedExpr):
            relatorio.anotar(
                caminho,
                no.lineno,
                "walrus",
                "O operador := confunde quem esta aprendendo. Escreva a "
                "atribuicao numa linha antes do if.",
            )

        if isinstance(no, (ast.Import, ast.ImportFrom)):
            nome_do_modulo = ""
            if isinstance(no, ast.ImportFrom) and no.module:
                nome_do_modulo = no.module
            elif isinstance(no, ast.Import):
                nome_do_modulo = no.names[0].name

            if nome_do_modulo in {"itertools", "operator"}:
                relatorio.anotar(
                    caminho,
                    no.lineno,
                    "modulo-proibido",
                    f"{nome_do_modulo} deixa o codigo enigmatico. Escreva o "
                    "laco na mao.",
                )

        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name):
            if no.func.id in {"eval", "exec"}:
                relatorio.anotar(
                    caminho,
                    no.lineno,
                    "eval",
                    f"{no.func.id}() executa texto como codigo. E porta aberta "
                    "para invasao.",
                )
            if no.func.id in {"map", "filter", "reduce"}:
                relatorio.anotar(
                    caminho,
                    no.lineno,
                    "map-filter",
                    f"{no.func.id}() e menos legivel que um for. Escreva o laco.",
                )

        if isinstance(no, ast.Global):
            relatorio.anotar(
                caminho,
                no.lineno,
                "global",
                "global esconde de onde o valor vem. Passe por parametro ou "
                "guarde num atributo.",
            )


def conferir_nome_curto(caminho: str, arvore: ast.Module, relatorio: Relatorio) -> None:
    """Regra: nome de uma letra nao diz nada para quem le depois."""
    abreviacoes_proibidas = {
        "r",
        "m",
        "v",
        "c",
        "e",
        "i",
        "j",
        "k",
        "n",
        "x",
        "y",
        "z",
        "msg",
        "ctx",
        "res",
        "req",
        "obj",
        "val",
        "tmp",
        "aux",
        "reg",
        "regs",
        "cfg",
        "usr",
        "btn",
        "ch",
        "gd",
    }

    for no in ast.walk(arvore):
        nomes_para_conferir: list[tuple[str, int]] = []

        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            todos_os_parametros = (
                no.args.args + no.args.kwonlyargs + no.args.posonlyargs
            )
            for parametro in todos_os_parametros:
                nomes_para_conferir.append((parametro.arg, parametro.lineno))

        if isinstance(no, ast.Name) and isinstance(no.ctx, ast.Store):
            nomes_para_conferir.append((no.id, no.lineno))

        for nome, numero_da_linha in nomes_para_conferir:
            if nome in NOMES_DA_API_EXTERNA:
                continue
            if nome.startswith("__"):
                continue
            if nome in abreviacoes_proibidas:
                relatorio.anotar(
                    caminho,
                    numero_da_linha,
                    "nome-curto",
                    f"O nome '{nome}' nao diz o que guarda. Escreva por "
                    "extenso, em portugues.",
                )


def conferir_id_magico(caminho: str, codigo: str, relatorio: Relatorio) -> None:
    """Regra: id de canal e cargo mora em src/config.py, so lá."""
    if caminho in {"src/config.py"}:
        return

    # Id do Discord tem 17 a 20 digitos.
    padrao_de_id = re.compile(r"(?<![\d.])\d{17,20}(?![\d.])")

    for numero, linha in enumerate(codigo.split("\n"), 1):
        texto_sem_espaco = linha.lstrip()
        if texto_sem_espaco.startswith("#"):
            continue
        if "http" in linha:
            # Link de mensagem do Discord carrega ids e nao e configuracao.
            continue
        if "placeholder" in linha or "Ex:" in linha or "Ex.:" in linha:
            # Texto de exemplo mostrado ao membro dentro de um formulario. Nao
            # e configuracao: e a dica de como preencher o campo.
            continue
        if padrao_de_id.search(linha):
            relatorio.anotar(
                caminho,
                numero,
                "id-magico",
                "Id de canal ou cargo escrito no meio do codigo. Coloque em "
                "CANAIS ou CARGOS, em src/config.py, com nome e comentario.",
            )


def conferir_funcao_longa(
    caminho: str, arvore: ast.Module, relatorio: Relatorio
) -> None:
    """Regra: funcao que nao cabe na tela faz mais de uma coisa."""
    for no in ast.walk(arvore):
        if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if no.end_lineno is None:
            continue

        quantidade_de_linhas = no.end_lineno - no.lineno
        if quantidade_de_linhas > LIMITE_DE_LINHAS_POR_FUNCAO:
            relatorio.anotar(
                caminho,
                no.lineno,
                "funcao-longa",
                f"A funcao '{no.name}' tem {quantidade_de_linhas} linhas "
                f"(o limite e {LIMITE_DE_LINHAS_POR_FUNCAO}). Separe em "
                "funcoes menores com nome descritivo.",
            )


def conferir_docstring(caminho: str, arvore: ast.Module, relatorio: Relatorio) -> None:
    """Regra: funcao publica sem docstring e caixa fechada."""
    if not ast.get_docstring(arvore):
        relatorio.anotar(
            caminho,
            1,
            "docstring-de-arquivo",
            "O arquivo nao tem docstring no topo explicando para que ele serve.",
        )

    for no in ast.walk(arvore):
        if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if no.name.startswith("_"):
            continue
        if no.name in NOMES_DA_API_EXTERNA:
            continue
        if ast.get_docstring(no):
            continue

        relatorio.anotar(
            caminho,
            no.lineno,
            "docstring",
            f"A funcao publica '{no.name}' nao tem docstring.",
        )


def conferir_nome_de_arquivo(caminho: str, relatorio: Relatorio) -> None:
    """Regra: arquivo de dominio se chama {dominio}_{tipo}.py."""
    if caminho in ARQUIVOS_FORA_DO_PADRAO_ACEITOS:
        return

    partes = caminho.split("/")
    if len(partes) < 3:
        return

    pasta_do_dominio = partes[1]
    if pasta_do_dominio in PASTAS_QUE_NAO_SAO_DOMINIO:
        return

    nome_do_arquivo = pathlib.Path(caminho).stem
    if nome_do_arquivo == "__init__":
        return

    termina_com_sufixo_conhecido = nome_do_arquivo.endswith(SUFIXOS_DE_ARQUIVO_ACEITOS)
    if not termina_com_sufixo_conhecido:
        relatorio.anotar(
            caminho,
            1,
            "nome-de-arquivo",
            f"'{nome_do_arquivo}.py' nao segue o padrao. Use um dos sufixos: "
            + ", ".join(SUFIXOS_DE_ARQUIVO_ACEITOS),
        )


def conferir_linha_longa(caminho: str, codigo: str, relatorio: Relatorio) -> None:
    """Regra: linha que passa da largura obriga a rolar de lado para ler."""
    for numero, linha in enumerate(codigo.split("\n"), 1):
        if len(linha) > LARGURA_MAXIMA_DE_LINHA:
            relatorio.anotar(
                caminho,
                numero,
                "linha-longa",
                f"A linha tem {len(linha)} caracteres (o limite e "
                f"{LARGURA_MAXIMA_DE_LINHA}).",
            )


# ---------------------------------------------------------------------------
# O laco principal
# ---------------------------------------------------------------------------


def conferir_o_projeto_inteiro() -> Relatorio:
    """Passa todas as regras em todos os arquivos de src."""
    relatorio = Relatorio()

    for arquivo in sorted(PASTA_FONTE.rglob("*.py")):
        if "__pycache__" in str(arquivo):
            continue

        caminho = str(arquivo.relative_to(PASTA_DO_PROJETO))
        codigo = arquivo.read_text(encoding="utf-8")

        conferir_nome_de_arquivo(caminho, relatorio)
        conferir_embed(caminho, codigo, relatorio)
        conferir_view_classico(caminho, codigo, relatorio)
        conferir_resposta_direta(caminho, codigo, relatorio)
        conferir_id_magico(caminho, codigo, relatorio)
        conferir_linha_longa(caminho, codigo, relatorio)

        try:
            arvore = ast.parse(codigo)
        except SyntaxError as erro_de_sintaxe:
            relatorio.anotar(
                caminho,
                erro_de_sintaxe.lineno or 1,
                "sintaxe",
                f"O arquivo nao compila: {erro_de_sintaxe.msg}",
            )
            continue

        conferir_except_silencioso(caminho, arvore, relatorio)
        conferir_print(caminho, arvore, relatorio)
        conferir_ferramenta_proibida(caminho, arvore, relatorio)
        conferir_nome_curto(caminho, arvore, relatorio)
        conferir_funcao_longa(caminho, arvore, relatorio)
        conferir_docstring(caminho, arvore, relatorio)

    return relatorio


# Regras que HOJE ainda tem pendencia conhecida. Elas sao mostradas no relatorio
# mas nao derrubam o guardiao, para o projeto poder ir zerando aos poucos sem
# viver com o CI vermelho. Tire a regra desta lista quando ela zerar.
REGRAS_QUE_AINDA_NAO_DERRUBAM = {
    "funcao-longa",
    "linha-longa",
    "docstring",
    "docstring-de-arquivo",
    "view-classico",
    "id-magico",
    "nome-curto",
}


def main() -> int:
    """Roda o guardiao e devolve o codigo de saida para o terminal."""
    leitor_de_argumentos = argparse.ArgumentParser(
        description="Confere as regras do AGENTS.md no codigo de src."
    )
    leitor_de_argumentos.add_argument(
        "--regra",
        help="Mostra so as reclamacoes desta regra (ex: embed, print).",
    )
    leitor_de_argumentos.add_argument(
        "--tudo",
        action="store_true",
        help="Falha tambem nas regras que ainda tem pendencia conhecida.",
    )
    argumentos = leitor_de_argumentos.parse_args()

    relatorio = conferir_o_projeto_inteiro()

    reclamacoes = relatorio.reclamacoes
    if argumentos.regra:
        reclamacoes = [
            reclamacao
            for reclamacao in reclamacoes
            if reclamacao.regra == argumentos.regra
        ]

    contagem = relatorio.contar_por_regra()

    print("=" * 72)
    print("GUARDIAO DAS REGRAS DO AGENTS.md")
    print("=" * 72)
    print()

    if not contagem:
        print("Nenhuma violacao encontrada. O codigo esta de acordo com o AGENTS.md.")
        return 0

    print("Placar por regra:")
    for regra in sorted(contagem, key=lambda chave: -contagem[chave]):
        marca = " (pendencia conhecida)" if regra in REGRAS_QUE_AINDA_NAO_DERRUBAM else ""
        print(f"  {contagem[regra]:>5}  {regra}{marca}")
    print()

    if argumentos.regra:
        print(f"Detalhe da regra '{argumentos.regra}':")
        for reclamacao in reclamacoes[:200]:
            print(f"  {reclamacao.arquivo}:{reclamacao.linha}")
            print(f"      {reclamacao.explicacao}")
        print()

    regras_que_derrubam = set(contagem)
    if not argumentos.tudo:
        regras_que_derrubam = regras_que_derrubam - REGRAS_QUE_AINDA_NAO_DERRUBAM

    if regras_que_derrubam:
        print("REPROVADO. Regras violadas que precisam ser corrigidas agora:")
        for regra in sorted(regras_que_derrubam):
            print(f"  - {regra} ({contagem[regra]})")
        print()
        print("Para ver o detalhe: python ferramentas/guardiao.py --regra NOME")
        return 1

    print("APROVADO nas regras obrigatorias.")
    print("Ainda ha pendencias conhecidas acima, que nao derrubam o guardiao.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
