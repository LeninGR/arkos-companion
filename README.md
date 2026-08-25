# ArkOS R36S ROM Manager & Optimizer

Herramienta de escritorio (PyQt6) para gestionar, visualizar, filtrar, eliminar y
auto-optimizar los juegos arcade/consola de la tarjeta microSD de un R36S con
ArkOS.

## Características

- Selección de la partición **EASYROMS** (contiene la carpeta `roms/`).
- **Detección automática al iniciar**: si la tarjeta está conectada y el volumen se
  llama `EASYROMS` (o contiene una carpeta `roms/`), se carga sola sin elegir nada.
- Detecta automáticamente los sistemas (MAME 2003, FBNeo, Neo Geo, GBA, SNES, …)
  y cuenta sus ROMs.
- Fusiona la información de `gamelist.xml` (ES-DE/EmulationStation): nombres reales,
  descripciones, imágenes y vídeos.
- **Estado de compatibilidad** en color:
  - Verde: el juego corre bien en esa carpeta.
  - Rojo: el juego está en una carpeta incorrecta y el optimizador sabe a dónde moverlo.
  - Gris: juego no catalogado.
- **Optimizar Emulador/Core**: mueve el juego a la carpeta recomendada por la base de
  datos interna y actualiza ambos `gamelist.xml`.
- **Eliminar juego por completo**: borra ROM, imagen, vídeo y su entrada en el gamelist.
- Vista previa de carátula y de vídeo (reproducción con QMediaPlayer).
- Tema oscuro, interfaz en español, operaciones pesadas en hilos (la UI nunca se congela).

## Instalación

```bash
cd arkos-companion
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Ejecución

```bash
python main.py
```

Pulsa **Seleccionar unidad/Carpeta EASYROMS** y elige la partición FAT32 de tu
tarjeta (o directamente la carpeta `roms/`).

## Ampliar la base de compatibilidad

Edita `arkos_companion/compat_db.py` y añade una entrada a `ARCADE_COMPATIBILITY`
con la clave = nombre base de la ROM (raíz del zip, en minúsculas). Los campos son:

- `name`: título real del juego.
- `system`: placa o plataforma.
- `bios`: lista de zips de BIOS necesarios (`[]` si ninguno).
- `works_in`: carpetas de ArkOS donde funciona bien en RK3326.
- `best_core`: core a escribir en `gamelist.xml`.
- `recommended_folder`: carpeta a la que el optimizador debe mover el juego.
- `bad_in`: carpetas donde el juego es incompatible (opcional).
- `note`: nota en español para el panel de detalles.

## API de TheGamesDB

El scraping necesita una API key de TheGamesDB (se solicita una vez en
[api.thegamesdb.net/key.php](https://api.thegamesdb.net/key.php)). La clave
**no está incluida en el repositorio**; se configura en tiempo de ejecución
por orden de precedencia:

1. `scraper_config.json` (junto al journal, ignorado por git) — clave por máquina.
2. Variable de entorno `THEGAMESDB_API_KEY`.
3. Constante `DEFAULT_API_KEY` en `arkos_companion/scraper.py` (vacía en el repo).

Sin clave configurada, el scraping se deshabilita y la UI muestra una ayuda
breve de configuración.

## Notas de seguridad

- Al sobrescribir por primera vez un `gamelist.xml` existente se guarda una copia
  `gamelist.xml.bak` junto al original (una sola vez por carpeta y sesión).
- **Eliminar** y **Optimizar** son operaciones destructivas: se pide confirmación
  antes de aplicarlas. Haz siempre una copia de seguridad de tu tarjeta.
- La reescritura del gamelist puede perder comentarios y el formato original del XML.
- La detección de carátulas/vídeos compara nombres por stem (ignorando acentos y
  espacios), lo que puede fallar si las imágenes tienen nombres muy distintos.