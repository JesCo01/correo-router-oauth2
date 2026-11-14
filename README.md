# Correo reenvio: PC administrador y Raspberry Pi lectora

Python 3.9+ sin dependencias externas. El PC configura cuentas, reglas y el login
Microsoft. La Raspberry solo lee, aplica reglas y reenvia.

## Como funciona, paso a paso

El recorrido es: **configurar en el PC → seleccionar cuentas → exportar →
copiar e instalar en la Pi → ejecutar el lector**. Cuando el lector esta
instalado en la Raspberry, puedes apagar el PC; la Pi necesita su propia
conexion a internet y estar encendida.

### 1. Preparas las cuentas y reglas en el PC

Abres el panel con `python -B panel.py`. Agregas cada cuenta, sus credenciales
y las reglas que indican a que destino reenviar los mensajes. Por ejemplo:
una cuenta Gmail puede reenviar facturas a contabilidad y otra cuenta Outlook
puede reenviar todos sus mensajes a una cuenta de respaldo.

Para Outlook, el inicio de sesion se hace desde el PC. Al completar la conexion
con Microsoft, se guarda el refresh token en `tokens/ID_CUENTA.json`.
La Raspberry no necesita navegador ni mostrar un formulario de login.

### 2. Eliges que cuentas usara la Raspberry

En la pestaña **Raspberry Pi** marcas las cuentas que quieres llevarte y guardas
los cambios. Por ejemplo, si tienes cinco cuentas y marcas dos, el paquete
llevara solo esas dos cuentas activas y sus reglas activas.

Las otras cuentas permanecen en el PC. Las cuentas asignadas a la Pi quedan
excluidas del lector local del PC. El boton del panel para iniciar el lector
del PC no inicia el servicio de la Raspberry.

### 3. Exportas una carpeta preparada para copiar

Pulsas **Exportar para Raspberry Pi**. El exportador lee la seleccion guardada,
comprueba que tenga reglas y credenciales, y genera `paquete_pi/`.

Dentro van el lector Python, la configuracion filtrada y los secretos necesarios.
`config.json` indica que cuentas leer y que reglas aplicar; `.env` contiene las
variables de acceso; `tokens/` contiene los refresh tokens de Outlook.
No necesitas copiar todo este proyecto a la Pi.

### 4. Copias e instalas en la Raspberry

Copias `paquete_pi/` por SSH o manualmente a una carpeta de transferencia.
Desde ella ejecutas `instalar_pi.py`, que prepara una instalacion separada,
por defecto en `~/correo-reenvio`. Las instrucciones completas y los comandos
estan en [la guia de instalacion](deploy/LEEME_PI.md).

Primero validas la configuracion, pruebas el acceso y ejecutas una simulacion.
La simulacion permite comprobar coincidencias sin reenviar mensajes. Despues
activas el servicio con systemd para que funcione al encender la Raspberry.

### 5. El lector trabaja de forma autonoma

Cada cuenta tiene un hilo. Outlook/Hotmail lee y envia por Microsoft Graph
mediante HTTPS y OAuth2, sin depender de que IMAP o SMTP esten activados.
Gmail conserva IMAP/SMTP. El lector busca pendientes, evalua filtros y elige
la primera ruta que coincide dentro de cada regla. Graph consulta cambios al
intervalo configurado en Ajustes (por defecto 300 segundos); Gmail puede usar IDLE.
El reenvio contiene una vista de texto y el mensaje original adjunto como `.eml`.

El progreso se guarda en `state/reglas.json`: cada regla recuerda el ultimo
progreso procesado: UID para Gmail, enlaces delta y mensajes pendientes para
Graph. Asi puede continuar despues de un reinicio. Por defecto, una
regla nueva empieza desde el momento en que se ejecuta por primera vez. Activa
**Procesar existentes** si tambien quieres recorrer el historial de esa carpeta.

### 6. Los tokens de Outlook se renuevan en la Pi

El access token es la credencial temporal usada para conectarse. El refresh
token permite solicitar otro acceso sin volver a mostrar el login. El lector
usa el tiempo `expires_in` devuelto por Microsoft para gestionar su cache de acceso.

Cuando necesita acceso nuevo, usa el refresh token guardado. Si Microsoft
devuelve tambien otro refresh token, la Pi reemplaza el anterior mediante una
escritura atomica. Por eso `tokens/` debe permitir escritura al usuario del lector.

Si la renovacion es rechazada porque hace falta iniciar sesion otra vez, esa
cuenta queda en `requiere_login`. Debes detener el servicio de la Pi, reconectar
la cuenta desde el PC y exportar su token nuevo. El procedimiento esta explicado
en la seccion **Refresh tokens** de este documento.

### 7. Actualizas sin perder el progreso

Cuando cambias cuentas o reglas en el PC, guardas, exportas y copias otro paquete.
Los cambios del panel no llegan automaticamente a la Raspberry.

Detienes el servicio, ejecutas el instalador y vuelves a iniciarlo. El instalador
actualiza el codigo y la configuracion, conserva `state/` y mantiene los tokens
existentes en la Pi. Usa `--renovar-token ID_CUENTA` solo para una cuenta que
hayas vuelto a conectar expresamente desde el PC.

## Administracion en el PC

Ejecuta `python -B panel.py` y abre http://127.0.0.1:8765.
Tambien puedes ejecutar `python -B admin/panel.py`.

La vista **Cuentas** muestra una tabla compacta con correo, proveedor, equipo y
estado. Pulsa **Nueva cuenta** o **Editar** para abrir el configurador de una
sola cuenta. **Guardar cuenta** guarda los datos y vuelve a la tabla;
**Cancelar** deshace los cambios de esa cuenta. Desde la tabla puedes conectar
Microsoft, probar el acceso o eliminar una cuenta.

No debes escribir un identificador por cuenta: se genera internamente y se
conserva al editar para mantener sus reglas y tokens. Tu correo es el nombre
visible. Todas las cuentas Outlook comparten el identificador global fijo de
Thunderbird; cada cuenta conserva sus propios tokens.

La vista **Reglas** tambien usa una tabla: nombre, cuenta, modo, destino y estado.
**Nueva regla** abre un solo configurador; el lapiz permite editar una regla
existente. **Guardar regla** guarda y vuelve a la tabla. **Cancelar** restaura
la regla original, incluidas sus rutas y filtros, o descarta una regla nueva.

Configura cuentas y reglas. Gmail usa contrasena de aplicacion. Para Outlook,
introduce tu correo y pulsa **Conectar con Microsoft** en su ficha. El proyecto
usa el identificador fijo de Thunderbird que utilizaba el script anterior:
`9e5f94bc-e8a4-4e73-b8be-63364c29d753`. No necesitas introducir un Client ID,
registrar una aplicacion ni instalar Thunderbird. El correo identifica la cuenta;
el identificador incorporado identifica la aplicacion en las solicitudes OAuth.
Se usa el mismo identificador para iniciar sesion y renovar los tokens.
Outlook usa Microsoft Graph con los permisos User.Read, Mail.ReadWrite,
Mail.Send y offline_access. No necesitas activar IMAP en Outlook.
Si autorizaste una version anterior, reinicia el panel y pulsa **Volver a
conectar** para aceptar los permisos de Graph. No se borra el token anterior
antes de terminar la nueva autorizacion. Si usas la Pi, vuelve a exportar y usa
`--renovar-token ID_CUENTA` al instalar. La primera ejecucion con Graph fija una
linea base nueva si no activaste Procesar existentes; no puede convertir un UID
IMAP en un cursor Graph. Se conserva una copia del progreso anterior.
Los cambios de Graph se procesan en el orden de su respuesta, no se garantiza
orden cronologico entre paginas.
[API de mensajes](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0).

En **Raspberry Pi**, selecciona las cuentas, guarda y pulsa **Exportar para
Raspberry Pi**. Tambien hay un interruptor en cada ficha de cuenta. Las cuentas
marcadas se excluyen del lector del PC. Cada cuenta activa seleccionada necesita
una regla activa y sus credenciales. Si el lector del PC aun procesa esas
cuentas, detenlo antes de exportar.

Se crea `paquete_pi/` con:

- `lector/`: solo Python, sin panel ni login interactivo.
- `config.json`: solo cuentas seleccionadas activas y sus reglas activas.
- `.env`: solo variables necesarias para esas cuentas.
- `tokens/`: solo refresh tokens de Outlook seleccionados.
- `instalar_pi.py`, unidad systemd, manifiesto y guia de uso.

No incluye `state/` del PC ni `__pycache__/`. El paquete contiene secretos y
esta excluido de Git. Protege tambien las copias de transferencia.

Alternativa por terminal, despues de seleccionar las cuentas y guardar:

```powershell
python -B exportar_pi.py
python -B exportar_pi.py --destino paquete_pi_nuevo
python -B exportar_pi.py --cuentas hotmail_principal gmail_trabajo
```

Si el paquete ya se uso como lector y contiene `state/`, exporta a otra carpeta.
Los destinos personalizados con secretos tambien deben excluirse de Git.

## Instalacion y actualizaciones

Sigue la [guia de la Pi](deploy/LEEME_PI.md). Copia el paquete a una carpeta de
transferencia separada de la instalacion y ejecuta:

```bash
cd ~/transferencia_correo
python3 -B instalar_pi.py --destino ~/correo-reenvio
cd ~/correo-reenvio
python3 -B -m lector.lector validar
python3 -B -m lector.lector probar
python3 -B -m lector.lector ejecutar --simular --medir-memoria
```

Puedes probar el paquete en Windows sustituyendo `python3` por `python`.
La simulacion no envia ni modifica el progreso de reglas; puede renovar tokens.
Sin "procesar existentes", una regla nueva fija su linea base y no muestra el
historial. Mide la memoria nuevamente en la Pi: Windows solo sirve de referencia.

Ajusta usuario y rutas en `correo-reenvio.service`, copialo a
`/etc/systemd/system/` y activa el servicio con systemd.

Para actualizar, deten el servicio, ejecuta el instalador desde el nuevo
paquete y reinicia. **No reemplaces la instalacion completa ni borres
`state/`.** El instalador conserva el progreso y los tokens renovados en la Pi.
Actualiza codigo, configuracion y las variables necesarias de `.env`.

## Refresh tokens

El lector obtiene acceso usando `refresh_token`, reutiliza el acceso segun
`expires_in` y guarda cada refresh token nuevo de forma atomica. No presupone
una duracion fija para el refresh token. Si Microsoft exige autorizacion nueva,
registra `requiere_login` y suspende esa cuenta; las demas continuan.

Deten la Pi, reconecta esa cuenta desde el panel del PC y exporta. Para sustituir
expresamente su token al instalar:

```bash
python3 -B instalar_pi.py --destino ~/correo-reenvio --renovar-token hotmail_principal
sudo systemctl start correo-reenvio
```

Puedes repetir `--renovar-token`. En actualizaciones normales no lo uses: el
token del PC puede ser anterior al de la Pi. Si tenias tokens de una aplicacion
propia diferente, reconecta esas cuentas para usar el identificador fijo.
[Documentacion de Microsoft](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens).

Un bloqueo impide dos lectores sobre el mismo directorio. Entre equipos,
mantiene un solo lector por cuenta: deten la Pi antes de probar o reconectar
esa cuenta desde el PC. Si devuelves una cuenta al PC, retirala primero de la
configuracion activa de la Pi. El panel muestra el estado local, no el de la Pi.

## Estructura y pruebas

- `admin/`: panel y login interactivo, solo PC.
- `lector/`: configuracion compartida, conexiones, OAuth no interactivo,
  filtros, estado, supervision, bloqueo y diagnostico de memoria.
- `exportar_pi.py`: arma el paquete desde la seleccion guardada.
- `deploy/`: instalador, guia y unidad systemd.
- `tests/`: pruebas sin red ni cuentas reales.
- `panel.py` y `correo_reenvio.py`: accesos compatibles.

```powershell
python -B -m unittest discover -s tests -v
```

El progreso se confirma tras cada envio, antes de marcar el mensaje como leido.
Un apagado entre la aceptacion del envio y la escritura del progreso aun puede duplicar
ese envio. "Procesar existentes" recorre todo el historial en lotes. El registro
rota a 128 KB con una copia. El limite de tamano se aplica al original; MIME
necesita memoria adicional.

Los viejos scripts usan ahora las cuentas configuradas en el panel, sin
credenciales incrustadas. `token_hotmail.json` se conserva, pero no se importa
automaticamente: reconecta la cuenta para autorizar Microsoft Graph. Si sigue vigente
la contrasena que estuvo escrita en el codigo, revocala.
