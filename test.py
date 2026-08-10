import os


def estrutura_pasta(caminho="", prefixo=""):
    """
    Retorna a estrutura de uma pasta como string formatada para terminal/chat

    Args:
        caminho (str): Caminho da pasta a ser analisada
        prefixo (str): Prefixo para indentação (uso interno)

    Returns:
        str: String formatada com a estrutura da pasta
    """
    resultado = []

    try:
        itens = sorted(os.listdir(caminho))
    except PermissionError:
        return f"{prefixo}[🚫 Acesso Negado]\n"
    except FileNotFoundError:
        return f"{prefixo}[❌ Pasta não encontrada]\n"

    for i, item in enumerate(itens):
        caminho_completo = os.path.join(caminho, item)
        eh_ultimo = i == len(itens) - 1

        # Define conectores visuais
        conector = "└── " if eh_ultimo else "├── "
        extensao = "    " if eh_ultimo else "│   "

        if os.path.isdir(caminho_completo):
            # É uma pasta
            resultado.append(f"{prefixo}{conector}📁 {item}/\n")
            # Recursão para subpastas
            resultado.append(estrutura_pasta(caminho_completo, prefixo + extensao))
        else:
            # É um arquivo
            tamanho = os.path.getsize(caminho_completo)
            tamanho_fmt = formatar_tamanho(tamanho)
            resultado.append(f"{prefixo}{conector}📄 {item} ({tamanho_fmt})\n")

    return "".join(resultado)


def formatar_tamanho(bytes_valor):
    """Formata tamanho de arquivo para leitura humana"""
    for unidade in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_valor < 1024:
            return f"{bytes_valor:.1f} {unidade}"
        bytes_valor /= 1024
    return f"{bytes_valor:.1f} TB"


def obter_estrutura_pasta(caminho="."):
    """
    Função principal que retorna a estrutura completa formatada

    Args:
        caminho (str): Caminho da pasta raiz

    Returns:
        str: Estrutura completa pronta para exibição
    """
    nome_pasta = os.path.basename(os.path.abspath(caminho))

    cabecalho = f"📂 {nome_pasta}/\n"
    estrutura = estrutura_pasta(caminho)

    return cabecalho + estrutura


# ============= EXEMPLOS DE USO =============

if __name__ == "__main__":
    estrutura = obter_estrutura_pasta(".\\src")
    with open("estrutura.txt", "w", encoding="utf-8") as f:
        f.write(estrutura)
"""    
# Exemplo 1: Pasta atual
print(obter_estrutura_pasta(".\\Projects\\cmsvalley-bot"))

    # Exemplo 2: Pasta específica
    print(obter_estrutura_pasta("./src"))"""

# Exemplo 3: Salvar em arquivo

"""    # Exemplo 4: Para usar em chat/terminal
    estrutura = obter_estrutura_pasta(".")
    print("```")
    print(estrutura)
    print("```")
"""
