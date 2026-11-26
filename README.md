# fclean

`fclean` es un script para liberar espacio del home de 42.

## Primer uso

Descarga el archivo `fclean` en `~/.config/fclean/`:

```bash
wget -P ~/.config/fclean/fclean https://raw.githubusercontent.com/Aimarr201/fclean/main/fclean
```

Antes de activar la limpieza automática, puedes revisar qué se borraría sin modificar ningún archivo:

```bash
bash ~/.config/fclean/fclean --dry-run
```

## Instalación recomendada

Para el uso habitual, se recomienda usar `--install`. Añade el alias `fclean` a la terminal y activa `--clean` al iniciar sesión gráfica.

Antes de limpiar, el script comprueba si hay una versión nueva. Si la encuentra, la descarga, la valida y la ejecuta, también al iniciar sesión. Si falla la actualización, usa la copia disponible.

```bash
bash ~/.config/fclean/fclean --install
```

Abre una nueva terminal para usar el alias desde cualquier carpeta. Ejecuta `fclean --clean` si quieres limpiar al momento.

Si ejecutaste `--install` desde otro directorio o un repositorio clonado, ya puedes borrar esa carpeta original porque el script queda guardado en `~/.config/fclean/fclean`.


## Uso sin instalar

Para ejecutar la limpieza en cualquier momento, desde la carpeta del script puedes ejecutar:

```bash
bash fclean --clean
```

- Para añadir solo el alias:

```bash
bash ~/.config/fclean/fclean --alias
```

Abre una nueva terminal Zsh para usar el alias desde cualquier carpeta.

## Qué hace

| Opción | Qué hace |
| --- | --- |
| `--dry-run` | Muestra qué se limpiaría, cuánto espacio se podría liberar y qué se puede migrar, sin modificar nada. |
| `--clean` | Limpia cachés, versiones obsoletas de extensiones, la papelera y los venvs que lleven 5 días sin actividad. |
| `--fclean` | Aplica la misma limpieza y borra de inmediato los venvs reconocidos, incluso si están activos. |
| `--migrate-cache` | Traslada `~/.cache` a `sgoinfre` y deja un enlace simbólico. |
| `--migrate` | Traslada las extensiones de VS Code a `sgoinfre` y deja un enlace simbólico. |
| `--alias` | Añade solo el alias de Zsh apuntando a `~/.config/fclean/fclean` (no copia el script). |
| `--install` | Copia el script en `~/.config/fclean/fclean` e instala el alias, el inicio automático y la configuración de Zsh para `sgoinfre`. |
| `--uninstall` | Desactiva el inicio automático y retira la configuración de `sgoinfre` de Zsh. Conserva el alias y el seguimiento de los venvs. |
| `--funinstall` | Retira el script instalado, el inicio automático, el alias, las copias de `.zshrc` y el seguimiento de los venvs. Conserva los venvs y los enlaces migrados a `sgoinfre`. |
| Sin opción, `-h` o `--help` | Muestra la ayuda. |

### Cachés y papelera

- Vacía la caché general de `~/.cache` y la de `sgoinfre` si el montaje está disponible. Incluye las entradas ocultas y procesa una sola vez una caché enlazada.
- Limpia cachés de npm, Bun, navegadores, editores y otras aplicaciones, incluidas las instalaciones Flatpak y Snap, aunque estén abiertas.
- Retira las versiones de extensiones marcadas en `.obsolete` que ya no figuran en los perfiles comprobados de VS Code.
- Omite archivos de otros propietarios, temporales `.nfs*`, montajes anidados y destinos de enlaces desconocidos.


### Entornos Python

Cuando `--clean` encuentra por primera vez un entorno virtual de Python asociado a un proyecto, empieza a registrar su uso. Solo lo borra tras cinco días completos sin actividad detectable en el entorno ni en el proyecto.

Para conservar un venv con `--clean`, crea este archivo en la raíz del proyecto o del venv.

```bash
touch .fclean-keep
```

`--fclean` se ejecuta solo de forma manual e ignora la asociación a proyectos.

### Migrar a sgoinfre

Si los problemas de espacio en `$HOME` son críticos, puedes migrar la caché y las extensiones.

> [!NOTE]
> Este proceso solo está recomendado como último recurso, ya que `sgoinfre` es un espacio común en el campus y no es permanente; haz un uso responsable del mismo.


```bash
fclean --migrate-cache
fclean --migrate
```

El script verifica los datos antes de sustituir la carpeta original por un enlace. Si hay dos cachés, usa `rsync` para combinar los archivos compatibles. Si detecta un conflicto, se detiene.

## Registro de la limpieza

Puedes consultar el registro de la limpieza automática del arranque actual con este comando:

```bash
journalctl --user -b -t 42-fclean.desktop --no-pager
```
