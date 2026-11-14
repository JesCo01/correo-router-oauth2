# Lector de correo para Raspberry Pi

Este paquete contiene secretos. No lo publiques ni lo subas a Git. Requiere
Python 3.9 o superior y no instala dependencias. No contiene panel web ni login
interactivo. El PC administra las cuentas; la Pi lee y reenvia.

## Primera instalacion

1. Deten el lector del PC para las cuentas seleccionadas. En el panel, marca
   las cuentas para Raspberry Pi, guarda y exporta.
2. Copia la carpeta a la Pi como directorio de transferencia separado:

```powershell
scp -r paquete_pi usuario@IP_PI:~/transferencia_correo
```

3. En la Pi:

```bash
chmod -R go-rwx ~/transferencia_correo
cd ~/transferencia_correo
python3 -B instalar_pi.py --destino ~/correo-reenvio
cd ~/correo-reenvio
python3 -B -m lector.lector validar
python3 -B -m lector.lector probar
python3 -B -m lector.lector ejecutar --simular --medir-memoria
```

`probar` comprueba Microsoft Graph para Outlook o IMAP para Gmail; puede
renovar el token, pero no envia correos. Outlook no requiere activar IMAP. La
simulacion consulta correos y reglas; no envia ni cambia el progreso de reglas.
Sin "procesar existentes", una regla nueva fija su linea base y la simulacion
no muestra el historial. La linea base real se fija al iniciar el lector.
No se garantiza un envio exactamente una vez ante un apagado entre la aceptacion
del envio y la escritura del progreso; se guarda cada envio confirmado para reducir
esa ventana.

4. Edita `User`, `WorkingDirectory` y `Documentation` en
   `~/correo-reenvio/correo-reenvio.service` para que correspondan al usuario y
   directorio elegidos. Despues:

```bash
sudo cp ~/correo-reenvio/correo-reenvio.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now correo-reenvio
sudo journalctl -u correo-reenvio -f
```

## Actualizacion

Exporta de nuevo desde el PC. Copia a una carpeta de transferencia nueva,
deten el servicio e instala; no copies el paquete encima de la instalacion:

```bash
sudo systemctl stop correo-reenvio
cd ~/transferencia_correo_nueva
python3 -B instalar_pi.py --destino ~/correo-reenvio
sudo systemctl start correo-reenvio
```

El instalador conserva `state/` y los tokens existentes; actualiza codigo,
configuracion y las variables necesarias de `.env`. El estado se asocia al ID de
regla, cuenta, direccion y carpeta; Gmail usa UIDVALIDITY y Graph usa enlaces
delta y una cola de pendientes. Conserva estos datos al actualizar
para mantener el progreso. El bloqueo impide actualizar mientras el lector corre
en ese directorio. No elimines ni sustituyas la instalacion completa.

## Reconectar Microsoft

El acceso se renueva con `refresh_token` usando `expires_in` para su cache.
Si Microsoft devuelve un nuevo refresh token, se guarda de forma atomica y con
permisos privados. No se presupone que el refresh token dure para siempre.

Si el registro indica `requiere_login`, deten el servicio en la Pi, reconecta esa
cuenta desde el panel del PC, exporta y copia el nuevo paquete. Para reemplazar
solo ese token:

```bash
python3 -B instalar_pi.py --destino ~/correo-reenvio --renovar-token hotmail_principal
sudo systemctl start correo-reenvio
```

Puedes repetir `--renovar-token` para varias cuentas. No uses esta opcion durante
actualizaciones normales: el token del PC puede ser anterior al renovado en la Pi.
El identificador de aplicacion es fijo y va incorporado en el lector. Si tus
tokens anteriores fueron emitidos para otra aplicacion, reconecta esas cuentas. No ejecutes lectores sobre las mismas cuentas en dos equipos.

`.env` y los archivos de tokens quedan con modo 600; `tokens/` con modo 700.
La Pi necesita escribir ahi para guardar la rotacion. Si otro equipo gana acceso
al paquete, puede usar sus credenciales: protege tambien las copias del PC y de
transferencia. En Windows aplica permisos de tu usuario a la carpeta.


## Migrar Outlook de IMAP a Microsoft Graph

Reinicia el panel del PC y vuelve a conectar las cuentas Outlook para autorizar
User.Read, Mail.ReadWrite, Mail.Send y offline_access. Se conserva el identificador
global de Thunderbird. Exporta el paquete, deten el servicio e instala con
`--renovar-token ID_CUENTA` para cada cuenta reconectada; despues reinicia el servicio.
El progreso IMAP se conserva como copia, pero Graph necesita una linea base nueva.
Si Procesar existentes esta desactivado, no se recorre el historial anterior a esa
primera ejecucion de Graph. Graph sondea segun Revision periodica (300 s por defecto).
