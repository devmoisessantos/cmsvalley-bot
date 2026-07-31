import cv2
import numpy as np
import easyocr
import re

CAMINHO_IMAGEM = "assets\\image.png"


def preprocessar_imagem(caminho: str) -> np.ndarray:
    imagem = cv2.imread(caminho)

    # Upscale maior, com interpolação melhor pra texto pequeno
    imagem = cv2.resize(imagem, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_LANCZOS4)
    cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)

    # Só contraste local, SEM binarização e SEM denoise agressivo
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    contraste = clahe.apply(cinza)

    cv2.imwrite("debug_processada.png", contraste)
    return contraste


def main():
    print("🔍 Pré-processando imagem...")
    imagem_processada = preprocessar_imagem(CAMINHO_IMAGEM)

    print("🔍 Carregando EasyOCR...")
    leitor = easyocr.Reader(["pt", "en"], gpu=False)

    print("🔍 Rodando OCR...")
    # allowlist restringe o alfabeto possível — reduz muito a confusão de caracteres
    resultados = leitor.readtext(
        imagem_processada,
        detail=1,  # 👈 agora com detalhe, pra ver a confiança de cada leitura
        paragraph=False,
        allowlist="0123456789:.- abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZÀÁÂÃÉÊÍÓÔÕÚÇàáâãéêíóôõúç",
        mag_ratio=2.0,  # magnifica ainda mais as regiões detectadas antes de reconhecer
    )

    print(f"\n✅ {len(resultados)} linhas brutas detectadas:\n")
    for bbox, texto, confianca in resultados:
        print(f"  [{confianca:.2f}] {texto}")

    print("\n🔍 Aplicando parser (regex ID: Nome)...")
    padrao = re.compile(r"(\d{3,7})\s*[:.\-]\s*(.+)")
    entradas = []
    for bbox, texto, confianca in resultados:
        match = padrao.search(texto.strip())
        if match:
            entradas.append((match.group(1).strip(), match.group(2).strip(), confianca))

    print(f"\n✅ {len(entradas)} entradas reconhecidas como ID: Nome:\n")
    for id_fivem, nome, confianca in entradas:
        print(f"  [{confianca:.2f}] {id_fivem}: {nome}")


if __name__ == "__main__":
    main()