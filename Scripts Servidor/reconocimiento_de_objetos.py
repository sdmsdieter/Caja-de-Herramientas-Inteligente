from ultralytics import YOLO

MODELO_ENTRENADO = "best_V3.pt"

INVENTARIO_BANDEJA_1 = [
    "llave_6",
    "llave_7",
    "llave_8",
    "llave_9",
    "llave_10",
    "llave_12",
    "llave_13",
    "llave_17",
    "llave_19",
    "llave_22",
]
INVENTARIO_BANDEJA_2 = [
    "estrella_grande",
    "estrella_peque",
    "estrella_mediano",
    "plano_grande",
    "plano_mediano",
    "plano_peque",
]

def analizar_inventario_ia(ruta_imagen, bandeja_id):
    """
    Analiza una imagen usando un modelo YOLOv8 entrenado.
    Devuelve un diccionario con los resultados y la ruta a la imagen con las detecciones.
    """
    try:
        model = YOLO(MODELO_ENTRENADO)
        results = model(ruta_imagen, conf=0.8, iou=0.5) #iou sirve para filtrar las cajas que se solapan, mediante el valor de interseccion sobre union
    except Exception as e:
        print(f"Error al cargar o usar el modelo YOLO: {e}")
        return {"error": str(e), "herramientas_detectadas": []}, None

    herramientas_detectadas = set()
    
    # Procesar los resultados
    for r in results:
        for box in r.boxes:
            # Obtener el ID de la clase detectada
            cls_id = int(box.cls[0])
            # Obtener el nombre de la herramienta a partir del ID
            nombre_herramienta = model.names[cls_id]
            herramientas_detectadas.add(nombre_herramienta)
    
    # Guardar la imagen con las detecciones dibujadas para enviarla de vuelta al usuario
    ruta_resultado = ruta_imagen.replace(".jpg", "_resultado.jpg")
    try:
        results[0].save(filename=ruta_resultado)
    except Exception as e:
        print(f"Error al guardar imagen de resultado: {e}")
        ruta_resultado = None # No se pudo guardar la imagen
    
    # El reporte ahora incluye el inventario ideal para esa bandeja,
    # lo que facilita la comparación en el servidor principal.
    reporte = {
        "bandeja_id": bandeja_id,
        "herramientas_detectadas": list(herramientas_detectadas),
        "inventario_ideal": INVENTARIO_BANDEJA_1 if str(bandeja_id) == '1' else INVENTARIO_BANDEJA_2
    }
    
    return reporte, ruta_resultado
