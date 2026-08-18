"""Protege fronteiras que já causaram importação circular e visual inconsistente."""

import ast
from pathlib import Path

DIRETORIO_RAIZ = Path(__file__).parents[1]
DIRETORIO_DO_CODIGO = DIRETORIO_RAIZ / "src"
CAMINHO_DO_MODULO_DE_MENSAGENS = DIRETORIO_DO_CODIGO / "utils" / "mensagens.py"
CAMINHO_DO_BOT = DIRETORIO_DO_CODIGO / "bot.py"


def obter_arquivos_python_do_codigo() -> list[Path]:
    """Lista arquivos Python do código-fonte, ignorando diretórios de cache."""
    return sorted(
        caminho_do_arquivo
        for caminho_do_arquivo in DIRETORIO_DO_CODIGO.rglob("*.py")
        if "__pycache__" not in caminho_do_arquivo.parts
    )


def obter_caminhos_dos_cogs_declarados_no_bot() -> list[str]:
    """Lê extensões estáticas sem importar o bot e iniciar recursos externos."""
    arvore_do_bot = ast.parse(CAMINHO_DO_BOT.read_text())

    for no_da_arvore in arvore_do_bot.body:
        if not isinstance(no_da_arvore, ast.Assign):
            continue
        if not any(
            isinstance(alvo_da_atribuicao, ast.Name)
            and alvo_da_atribuicao.id == "CAMINHOS_DOS_COGS"
            for alvo_da_atribuicao in no_da_arvore.targets
        ):
            continue
        return [
            elemento_da_lista.value
            for elemento_da_lista in no_da_arvore.value.elts
            if isinstance(elemento_da_lista, ast.Constant)
            and isinstance(elemento_da_lista.value, str)
        ]

    raise AssertionError("CAMINHOS_DOS_COGS precisa existir em src/bot.py.")


def teste_modulo_de_mensagens_nao_importa_src_para_evitar_ciclo_de_importacao():
    texto_do_modulo_de_mensagens = CAMINHO_DO_MODULO_DE_MENSAGENS.read_text()
    arvore_do_modulo_de_mensagens = ast.parse(texto_do_modulo_de_mensagens)
    importacoes_do_pacote_src: list[str] = []

    for no_da_arvore in ast.walk(arvore_do_modulo_de_mensagens):
        if isinstance(no_da_arvore, ast.Import):
            for nome_importado in no_da_arvore.names:
                if nome_importado.name == "src" or nome_importado.name.startswith(
                    "src."
                ):
                    importacoes_do_pacote_src.append(nome_importado.name)
        if isinstance(no_da_arvore, ast.ImportFrom):
            if no_da_arvore.module == "src" or (
                no_da_arvore.module and no_da_arvore.module.startswith("src.")
            ):
                importacoes_do_pacote_src.append(no_da_arvore.module)

    assert not importacoes_do_pacote_src, (
        "mensagens.py é a camada base e não pode importar módulos de src."
    )


def teste_nenhum_modulo_do_codigo_cria_discord_embed_fora_da_camada_de_componentes():
    arquivos_com_embed: list[str] = []

    for caminho_do_arquivo in obter_arquivos_python_do_codigo():
        arvore_do_arquivo = ast.parse(caminho_do_arquivo.read_text())

        for no_da_arvore in ast.walk(arvore_do_arquivo):
            if not isinstance(no_da_arvore, ast.Call):
                continue
            funcao_chamada = no_da_arvore.func
            if not isinstance(funcao_chamada, ast.Attribute):
                continue
            if not isinstance(funcao_chamada.value, ast.Name):
                continue
            if funcao_chamada.value.id != "discord":
                continue
            if funcao_chamada.attr == "Embed":
                arquivos_com_embed.append(
                    str(caminho_do_arquivo.relative_to(DIRETORIO_RAIZ))
                )

    assert not arquivos_com_embed, (
        "Components V2 substitui Embeds; ocorrências encontradas: "
        f"{arquivos_com_embed}."
    )


def teste_todo_caminho_de_cog_do_bot_tem_arquivo_e_funcao_setup_assincrona():
    caminhos_dos_cogs = obter_caminhos_dos_cogs_declarados_no_bot()
    problemas_encontrados: list[str] = []

    for caminho_do_cog in caminhos_dos_cogs:
        caminho_do_arquivo = DIRETORIO_RAIZ.joinpath(
            *caminho_do_cog.split(".")
        ).with_suffix(".py")

        if not caminho_do_arquivo.is_file():
            problemas_encontrados.append(f"{caminho_do_cog}: arquivo ausente")
            continue

        arvore_do_cog = ast.parse(caminho_do_arquivo.read_text())
        possui_setup_assincrono = any(
            isinstance(no_da_arvore, ast.AsyncFunctionDef)
            and no_da_arvore.name == "setup"
            for no_da_arvore in arvore_do_cog.body
        )

        if not possui_setup_assincrono:
            problemas_encontrados.append(f"{caminho_do_cog}: async def setup ausente")

    assert not problemas_encontrados, (
        "Toda extensão declarada deve existir e expor setup assíncrono: "
        f"{problemas_encontrados}."
    )
