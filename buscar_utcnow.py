import os

for pasta_raiz, _, arquivos in os.walk("src"):
    for nome_arquivo in arquivos:
        if nome_arquivo.endswith(".py"):
            caminho = os.path.join(pasta_raiz, nome_arquivo)
            with open(caminho, encoding="utf-8") as f:
                for numero_linha, linha in enumerate(f, start=1):
                    if "utcnow()" in linha:
                        print(f"{caminho}:{numero_linha}: {linha.strip()}")