# fclean

`fclean` ayuda a liberar espacio en los 5 GB de `$HOME` de los equipos Ubuntu del campus 42. Puede limpiar cachés al iniciar sesión y trasladar las extensiones de VS Code o la caché general a `$HOME/sgoinfre/$USER`. Cada migración se activa por separado.

## Primer uso en el campus

En Ubuntu, abre una terminal en la carpeta que contiene el script. Con tu cuenta del campus y sin `sudo`, ejecuta la vista previa.

```bash
bash fclean --dry-run
```

La vista previa corresponde a `--clean` e indica por separado las migraciones opcionales. Los tamaños son estimaciones. Si una carpeta ya está en `sgoinfre`, vaciarla libera espacio allí. Mover una carpeta fuera del home libera cuota en el home aunque se conserve su contenido.

Si quieres trasladar datos, cierra primero las aplicaciones que los usan **en todos los equipos donde tengas abierta la misma cuenta**. Puedes ejecutar cualquiera de estas migraciones por separado o hacer ambas.

```bash
bash fclean --migrate-cache   # Conserva la caché actual y enlaza ~/.cache
bash fclean --migrate         # Traslada y enlaza ~/.vscode/extensions
```

Activa la limpieza automática cuando hayas revisado qué se borrará.

```bash
bash fclean --install
```

La primera limpieza automática ocurre en el siguiente inicio de sesión gráfica. Si quieres limpiar ahora, ejecuta `bash fclean --clean`. **Ambas formas vacían la papelera sin confirmación.**

## Opciones

El script acepta una opción por ejecución.

| Opción | Qué hace |
| --- | --- |
| Sin opción o `--dry-run` | Muestra qué limpiaría y qué podría migrarse. Es una operación de solo lectura. |
| `--clean` | Vacía las rutas seleccionadas y limpia los entornos Python de proyectos que cumplen la regla de cinco días. |
| `--fclean` | Limpia las cachés conocidas aunque las aplicaciones estén abiertas y conserva las extensiones instaladas de VS Code. Borra los venvs reconocidos en el home sin esperar cinco días. Se ejecuta manualmente. |
| `--migrate-cache` | Copia y verifica `~/.cache` antes de sustituirla por un enlace a `$HOME/sgoinfre/$USER/.cache`. Si ambas carpetas existen, usa `rsync` para combinarlas y se detiene si encuentra un conflicto. |
| `--migrate` | Copia y verifica `~/.vscode/extensions` y deja un enlace a `$HOME/sgoinfre/$USER/.vscode/extensions`. Si las extensiones de origen y destino difieren, conserva ambas carpetas. |
| `--install` | Instala o actualiza la copia del script, registra `--clean` en el inicio de sesión gráfica y configura el entorno de Zsh. |
| `--uninstall` | Retira el inicio automático, el bloque de Zsh y los archivos de arranque de seguimiento que reconoce en los venvs del home. |
| `--help` o `-h` | Muestra la ayuda. |

## Qué se borra en cada limpieza

**La caché general se limpia antes y después de migrarla.** Tanto `--clean` como `--dry-run` comprueban `~/.cache` y `$HOME/sgoinfre/$USER/.cache` si el montaje es válido. Si son dos directorios distintos, procesan ambos. Si el home tiene un enlace a la caché migrada, la procesan una sola vez. Esto también funciona sin los exports de Zsh.

Estas rutas se limpian incluso si la aplicación está abierta o existe un `SingletonLock`. La regla equivale a seleccionar `carpeta/*` en Bash. Conserva la carpeta y las entradas ocultas de su primer nivel. Dentro de cada directorio visible seleccionado también elimina los archivos ocultos.

| Ubicación | Rutas seleccionadas |
| --- | --- |
| Papelera | `~/.local/share/Trash/*`, incluidos los archivos descartados y sus datos de recuperación. |
| Caché general y Flatpak | El contenido de `~/.cache` y de `~/.var/app/*/cache`, incluidas las cachés de Spotify, Firefox, Chrome, VS Code y Discord. |
| npm y Bun | `~/.npm/_cacache` y `~/.bun/install/cache`. Una instalación posterior puede volver a descargar paquetes. |
| Chrome Flatpak | Bajo `~/.var/app/com.google.Chrome/config/google-chrome`, las carpetas `component_crx_cache` y `extensions_crx_cache`. En `Default` y `Profile 0` a `Profile 9`, también `File System`, `Service Worker/ScriptCache` y `Service Worker/CacheStorage`. |
| VS Code y Discord Flatpak | `Cache`, `CachedData` y `CachedExtensionVSIXs` bajo `com.visualstudio.code/config/Code`, y `Cache` bajo `com.discordapp.Discord/config/discord`. |

**Todo proyecto o entorno virtual enviado a la papelera se pierde al vaciarla.** La misma regla alcanza los modelos, los tokens de Hugging Face y cualquier otro archivo guardado dentro de un directorio visible de caché. `File System` y `CacheStorage` pueden contener trabajo local de una web y datos para usarla sin conexión.

Para estas ubicaciones adicionales, `--clean` comprueba qué aplicaciones y perfiles están en uso. `--fclean` limpia las mismas rutas incluso con procesos activos, perfiles con `SingletonLock` o aplicaciones Flatpak abiertas.

| Aplicación | Cachés adicionales |
| --- | --- |
| Chrome, Chromium y Brave | `component_crx_cache`, `extensions_crx_cache` y, en `Default` y cada `Profile N` numérico, `Cache`, `Code Cache`, `GPUCache`, `File System`, `Service Worker/ScriptCache` y `Service Worker/CacheStorage`. |
| Firefox y Chromium en Snap | `~/snap/firefox/common/.cache` y `~/snap/chromium/common/.cache`. |
| Slack y Discord | `Cache`, `Code Cache`, `GPUCache` y las carpetas `ScriptCache` y `CacheStorage` de los service workers. |
| VS Code y VSCodium | `Cache`, `Code Cache`, `GPUCache`, `CachedData` y los instaladores de `CachedExtensionVSIXs`. En las extensiones migradas, y también en las locales con `--fclean`, solo se buscan archivos de caché antiguos con el formato de descarte de Chromium. |

El recorrido se limita a rutas validadas del usuario. Omite entradas de otros propietarios, archivos temporales `.nfs*`, montajes anidados y destinos de enlaces simbólicos ajenos a las migraciones reconocidas. La vista previa aplica las mismas reglas que la limpieza. Una aplicación abierta puede volver a crear archivos mientras se limpia su caché.

## Limpieza manual con `--fclean`

`bash fclean --fclean` borra sin confirmación los **venvs reconocidos en el home**, incluso si están activos, son recientes, contienen `.fclean-keep` o carecen de un proyecto asociado. También busca entornos en las carpetas de herramientas como Poetry y pipx. Un directorio que mezcla el entorno y archivos de proyecto en su raíz queda fuera de esta regla.

Vacía la caché general, incluidas sus entradas ocultas, tanto en `~/.cache` como en `$HOME/sgoinfre/$USER/.cache`, donde existan. Conserva las extensiones instaladas de VS Code, sus índices y los entornos Python que distribuyen, tanto en el home como en `sgoinfre`.

En Linux se puede borrar la entrada de un archivo que una aplicación tiene abierto. El espacio que ocupa puede seguir en uso hasta que se cierre el último descriptor. En un home NFS pueden quedar temporalmente archivos `.nfs*`; el script los omite. La aplicación también puede volver a crear su caché durante la misma sesión. [Manual de Linux sobre unlink](https://man7.org/linux/man-pages/man2/unlink.2.html)

El inicio de sesión instalado con `--install` sigue ejecutando `--clean`.

## Entornos Python y plazo de cinco días

`--clean` busca en el home entornos Python 3 que pertenezcan al usuario. Reconoce un `pyvenv.cfg` con la clave `home`, un `bin/python` ejecutable y el directorio `lib/python3.X/site-packages`. El nombre del entorno es libre. Si su raíz contiene archivos de proyecto mezclados con el entorno, la conserva. Las cachés también respetan los directorios con `pyvenv.cfg`. La papelera mantiene su regla de vaciado explícito. [Estructura de un venv según Python](https://docs.python.org/3/library/venv.html)

1. **Asociación al proyecto.** Busca un repositorio Git entre los directorios superiores, un manifiesto de Python o archivos `.py` junto al directorio del venv. Un entorno centralizado, como `~/Python_envs/Py09_env`, se conserva si no puede asociarse a un proyecto. El recorrido excluye `sgoinfre`, otros montajes, cachés y las instalaciones propias de herramientas como Poetry y pipx.
2. **Primer encuentro.** `--clean` coloca `fclean_venv_usage_v1.pth` y `_fclean_venv_usage_v1.py` en el `site-packages` del entorno. El plazo empieza en ese momento, incluso para un venv creado hace meses. `--dry-run` muestra qué entornos empezarían el seguimiento y cuáles se borrarían, sin instalar archivos.
3. **Actividad.** Al arrancar Python normalmente, incluso desde un IDE o sin activar el venv, el entorno actualiza la marca y crea un archivo temporal en el home compartido. Mientras ese archivo exista se conserva el entorno. Al cerrar Python normalmente, el plazo vuelve a empezar. Un cierre forzado puede dejar una marca pendiente. Si corresponde a un proceso desaparecido del mismo arranque de Ubuntu, la limpieza concede otros cinco días. Una marca de otro puesto que no se puede verificar protege el venv.
4. **Borrado.** Deben pasar **120 horas completas** sin uso registrado, sin señales recientes en los archivos del venv o del proyecto y sin procesos locales detectados que lo utilicen. Editar hoy un archivo del proyecto protege el entorno aunque hayas pasado una semana sin ejecutar Python. Se elimina únicamente el directorio del venv.

**Para proteger el venv de un proyecto frente a `--clean`**, crea `.fclean-keep` en la raíz del proyecto o del propio entorno. Es especialmente útil con `python -S`, que omite el seguimiento de arranque. También cubre sesiones de otro puesto que ya estaban abiertas antes de instalar el seguimiento, ediciones sin guardar y accesos desde un montaje de solo lectura. En NFS, algunas lecturas servidas desde caché tampoco actualizan la fecha que ve el servidor. [Arranque de Python](https://docs.python.org/3/library/site.html) · [Fechas de acceso en NFS](https://man7.org/linux/man-pages/man5/nfs.5.html)

En Ubuntu puedes comprobar ambas opciones con `python3 -m unittest discover -s tests` desde el repositorio. Las pruebas usan un home temporal, simulan el paso de los días y comprueban qué se borra con las carpetas de migración separadas o enlazadas.

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
