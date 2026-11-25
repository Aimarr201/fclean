# Publicar fclean

Publica desde `dev` con una etiqueta `publicar-*`. GitHub Actions prueba el commit etiquetado y, si las pruebas pasan, copia de ese mismo commit `fclean` y `README.md` a `main`.


Crea una etiqueta nueva para el commit que acabas de subir. Cambia `v1.0.0` por la versión que quieras publicar.

```bash
git switch dev
git tag publicar-v1.0.0 dev
git push origin publicar-v1.0.0
```
