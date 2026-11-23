# fclean

`fclean` ayuda a liberar espacio en los 5 GB de `$HOME` de los equipos Ubuntu del campus 42. Puede limpiar cachés al iniciar sesión y trasladar las extensiones de VS Code o la caché general a `$HOME/sgoinfre/$USER`. Cada migración se activa por separado.

## Primer uso en el campus

En Ubuntu, abre una terminal en la carpeta que contiene el script. Con tu cuenta del campus y sin `sudo`, ejecuta la vista previa.

```bash
bash fclean --dry-run
```

La salida separa el contenido que se borraría de las migraciones opcionales. Los tamaños en KiB son estimaciones. Si una carpeta ya está en `sgoinfre`, vaciarla libera espacio allí. Mover una carpeta fuera del home libera cuota en el home aunque se conserve su contenido.

Si quieres trasladar datos, cierra primero las aplicaciones que los usan **en todos los equipos donde tengas abierta la misma cuenta**. Puedes ejecutar cualquiera de estas migraciones por separado o hacer ambas.

```bash
bash fclean --migrate-cache   # Conserva la caché actual y enlaza ~/.cache
bash fclean --migrate         # Traslada y enlaza ~/.vscode/extensions
```

Activa la limpieza automática cuando hayas revisado qué se borrará.

```bash
bash fclean --install
```

La primera limpieza automática ocurre en el siguiente inicio de sesión gráfica. Si quieres limpiar ahora, ejecuta `bash fclean --clean`. El borrado se aplica sin confirmación.

## Opciones

El script acepta una opción por ejecución.

| Opción | Qué hace |
| --- | --- |
| Sin opción o `--dry-run` | Muestra qué limpiaría y qué podría migrarse. Es una operación de solo lectura. |
| `--clean` | Vacía las rutas seleccionadas del home y de los destinos gestionados en `sgoinfre`. |
| `--migrate-cache` | Copia y verifica `~/.cache` antes de sustituirla por un enlace a `$HOME/sgoinfre/$USER/.cache`. Si ambas carpetas existen, usa `rsync` para combinarlas y se detiene si encuentra un conflicto. |
| `--migrate` | Copia y verifica `~/.vscode/extensions` y deja un enlace a `$HOME/sgoinfre/$USER/.vscode/extensions`. Si las extensiones de origen y destino difieren, conserva ambas carpetas. |
| `--install` | Instala o actualiza la copia del script, registra `--clean` en el inicio de sesión gráfica y configura el entorno de Zsh. |
| `--uninstall` | Retira el inicio automático y el bloque de Zsh. La copia instalada, los enlaces y los datos migrados se conservan. |
| `--help` o `-h` | Muestra la ayuda. |

## Qué se borra en cada limpieza

| Ubicación | Contenido que puede vaciarse |
| --- | --- |
| Caché general y Flatpak | El contenido de `~/.cache` y de `~/.var/app/*/cache`, incluidas las cachés de Spotify, Firefox, Chrome, VS Code y Discord. |
| npm y Bun | `~/.npm/_cacache` y `~/.bun/install/cache`. Una instalación posterior puede volver a descargar paquetes. |
| Chrome, Chromium y Brave | `component_crx_cache`, `extensions_crx_cache` y, en `Default` y cada `Profile N` numérico, `Cache`, `Code Cache`, `GPUCache`, `File System`, `Service Worker/ScriptCache` y `Service Worker/CacheStorage`. |
| Firefox y Chromium en Snap | `~/snap/firefox/common/.cache` y `~/snap/chromium/common/.cache`. |
| Slack y Discord | `Cache`, `Code Cache`, `GPUCache` y las carpetas `ScriptCache` y `CacheStorage` de los service workers. |
| VS Code y VSCodium | `Cache`, `Code Cache`, `GPUCache`, `CachedData` y los instaladores de `CachedExtensionVSIXs`. Dentro de las extensiones migradas solo se buscan archivos de caché antiguos que Chromium haya descartado. |

**El borrado de `File System` y `CacheStorage` puede eliminar trabajo guardado por una web en el equipo y contenido disponible sin conexión.** La limpieza conserva la papelera y omite los repositorios y entornos virtuales que reconoce. También omite las cachés de aplicaciones que detecta en uso. Los modelos de Hugging Face y otras descargas guardadas bajo `~/.cache` pueden tener que descargarse otra vez.

## Cómo se usa sgoinfre

Todo lo que el script escribe en `sgoinfre` queda bajo `$HOME/sgoinfre/$USER`. Antes de escribir, comprueba que el almacenamiento es distinto del home y que se puede escribir en él. Si falta el montaje, la limpieza local sigue disponible y las migraciones se detienen.

Al abrir una nueva terminal Zsh después de instalar, el bloque de `~/.zshrc` prepara los directorios y, si están disponibles, aplica estas variables.

```bash
SGO="$HOME/sgoinfre/$USER"
export XDG_CACHE_HOME="$SGO/.cache"
export XDG_DATA_HOME="$SGO/.local/share"
export UV_CACHE_DIR="$SGO/.cache/uv"
export HF_HOME="$SGO/.cache/huggingface"
```

Las aplicaciones iniciadas desde esa terminal pueden usar esas rutas. `XDG_DATA_HOME` dirige los nuevos datos persistentes a `sgoinfre` en las aplicaciones que lo respetan. El contenido previo de `~/.local/share` sigue en su ubicación. Para que las aplicaciones que escriben directamente en `~/.cache` usen también `sgoinfre`, hace falta `--migrate-cache`.

Las limpiezas posteriores vacían también la caché trasladada o configurada por el instalador, con las mismas exclusiones y sin umbral de tamaño. Si el campus vacía `sgoinfre`, el script puede recrear destinos vacíos de enlaces conocidos. Tendrás que reinstalar las extensiones perdidas y recuperar de una copia los datos persistentes que necesites.

## Actualizar o desactivar

Cuando cambies de versión, vuelve a ejecutar `bash fclean --install` desde la copia nueva. Por defecto, la copia instalada está en `~/.config/fclean/fclean` y la entrada de inicio en `~/.config/autostart/42-fclean.desktop`. Si has definido `XDG_CONFIG_HOME`, ambas rutas parten de ese directorio. Cuando modifica un `~/.zshrc` existente, el instalador guarda una copia junto al archivo.

Para desactivar la limpieza automática y retirar los exports, ejecuta `bash fclean --uninstall`.
