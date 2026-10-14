# Desplegar GameTrack en Google Cloud

Implementación del diseño de [GOOGLE_CLOUD.md](../../GOOGLE_CLOUD.md).
Instalación del 7 de octubre de 2026 en `gametrack-510920`, región
`us-central1`. [Abrir GameTrack](https://gametrack-591398735968.us-central1.run.app).
PostgreSQL, buckets privados, secretos, web, jobs y presupuesto ya están
creados. Los resultados se registran en [DEPLOYMENT.md](DEPLOYMENT.md).
El procedimiento siguiente permite reproducir y mantener
la instalación. Ejecutar `terraform apply` crea servicios facturables.

## Qué debe completar el titular de la cuenta

1. Activar la [prueba gratuita](https://cloud.google.com/free) y completar la
   verificación directamente en Google. No compartir contraseña ni tarjeta.
2. Crear un proyecto llamado GameTrack, vincularlo a la cuenta de prueba y
   comunicar su **ID de proyecto**. La región inicial es `us-central1`.
3. Autorizar una sesión de Google Cloud CLI o Cloud Shell con esa cuenta.
   No descargar ni compartir claves JSON de cuentas de servicio.

El operador necesita permiso para administrar los recursos del proyecto,
crear cuentas de servicio y asignar sus roles. Para el presupuesto también
necesita administrar presupuestos de la cuenta de facturación. Una política
de organización puede impedir el acceso público de Cloud Run; comprobarlo
si el proyecto no pertenece a una cuenta personal.

## Archivos incluidos

| Archivo | Función |
|---|---|
| `main.tf` | Base PostgreSQL, buckets privados, imágenes, identidades, secretos y presupuesto. |
| `run.tf` | Web y jobs de migración, copia, Steam y entrenamiento; cron autenticado. |
| `settings.py` | Guarda parámetros no secretos conservando etapas previas. |
| `configure_secrets.py` | Genera contraseña SQL y clave JWT; carga clave Steam sin mostrarlas. |
| `snapshot_sqlite.py` | Backup consistente de SQLite, incluyendo contenido del WAL. |
| `cloudbuild.yaml` | Construye y publica el contenedor usando una identidad dedicada. |
| `smoke_check.py` | Verifica la web, roles, recomendaciones y callback de Steam sin mostrar tokens. |

El estado se guarda en un bucket privado de Terraform. Las contraseñas y
valores de secretos no forman parte de ese estado. `settings.auto.tfvars.json`,
planes, credenciales, bases y entornos Python se excluyen de Git, Docker y de
la subida a Cloud Build.

## Ejecución por etapas

En esta PC las herramientas portables están en `deploy/google-cloud/.tools`.
Para iniciar la sesión de PowerShell preparada para el proyecto:

```powershell
. ./deploy/google-cloud/session.ps1
gcloud auth login --update-adc
```

La autenticación se completa directamente en el navegador de Google. La sesión
usa una carpeta privada de configuración dentro de `.tools`, excluida de Git,
Docker y Cloud Build; no requiere compartir contraseñas ni claves JSON. Volver
a cargar `session.ps1` después del login para que Terraform use esas credenciales.

Los comandos siguientes son para un operador en **Cloud Shell/Linux**, desde
una copia del código actual. Cloud Shell incluye Google Cloud CLI y Terraform;
comprobar su versión con `terraform version` (mínimo 1.6). Se puede usar la
misma configuración desde Windows con esas herramientas instaladas.
No usar una copia antigua del repositorio que todavía no tenga estos archivos.

Definir el ID real y mantener esa sesión/copia durante todas las etapas:

```bash
export PROJECT_ID='ID_REAL_DEL_PROYECTO'
export REGION='us-central1'
gcloud projects describe "$PROJECT_ID"
gcloud billing projects describe "$PROJECT_ID"
gcloud auth application-default login
```

Verificar que la facturación corresponde a la prueba del titular. Los
comandos no activan automáticamente una cuenta de pago.

### 1. Estado remoto e infraestructura

Crear una sola vez el bucket de estado. Si ya existe, verificar propietario,
acceso privado y ubicación, y reutilizarlo; no eliminarlo para repetir init.

```bash
gcloud services enable storage.googleapis.com serviceusage.googleapis.com cloudresourcemanager.googleapis.com --project="$PROJECT_ID"
gcloud storage buckets create "gs://${PROJECT_ID}-gametrack-tfstate" \
  --project="$PROJECT_ID" --location="$REGION" \
  --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://${PROJECT_ID}-gametrack-tfstate" --versioning

export BILLING_ACCOUNT_ID="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingAccountName)' | sed 's|billingAccounts/||')"
python3 deploy/google-cloud/settings.py --project "$PROJECT_ID" --region "$REGION" \
  --billing-account "$BILLING_ACCOUNT_ID"
terraform -chdir=deploy/google-cloud init \
  -backend-config="bucket=${PROJECT_ID}-gametrack-tfstate" -backend-config='prefix=gametrack'
terraform -chdir=deploy/google-cloud plan -out=foundation.tfplan
terraform -chdir=deploy/google-cloud apply foundation.tfplan
```

La primera aplicación crea sólo la infraestructura. Web, jobs y Scheduler
quedan deshabilitados. Conservar el bucket de estado para futuras actualizaciones.
Si falta permiso para el presupuesto, configurarlo con el titular antes de
habilitar las ejecuciones; no ignorar ese error.

### 2. Secretos e imagen

El helper pide la clave Steam con entrada oculta si no existe en Secret Manager.
Desde esta PC se puede usar `--steam-env-file backend/.env` para reutilizarla
sin copiarla a una conversación. No subir `.env` a Cloud Shell ni Cloud Build.

```bash
export CONNECTION_NAME="$(terraform -chdir=deploy/google-cloud output -raw connection_name)"
python3 deploy/google-cloud/configure_secrets.py --project "$PROJECT_ID" \
  --connection-name "$CONNECTION_NAME"

export IMAGE_TAG="${REGION}-docker.pkg.dev/${PROJECT_ID}/gametrack/app:$(date -u +%Y%m%d%H%M%S)"
gcloud meta list-files-for-upload
gcloud builds submit . --project="$PROJECT_ID" --region="$REGION" \
  --config=deploy/google-cloud/cloudbuild.yaml \
  --service-account="projects/${PROJECT_ID}/serviceAccounts/gametrack-build@${PROJECT_ID}.iam.gserviceaccount.com" \
  --gcs-source-staging-dir="gs://${PROJECT_ID}-gametrack-builds/sources" \
  --substitutions="_IMAGE=${IMAGE_TAG}"
export IMAGE_DIGEST="$(gcloud artifacts docker images describe "$IMAGE_TAG" --project="$PROJECT_ID" --format='value(image_summary.digest)')"
python3 deploy/google-cloud/settings.py \
  --image="${REGION}-docker.pkg.dev/${PROJECT_ID}/gametrack/app@${IMAGE_DIGEST}" --enable-jobs
terraform -chdir=deploy/google-cloud plan -out=jobs.tfplan
terraform -chdir=deploy/google-cloud apply jobs.tfplan
```

Antes de subir, comprobar que la lista excluye `.env`, bases, backups, entornos
virtuales, estado y planes. La imagen se fija por digest para que web y jobs
ejecuten exactamente la misma versión.

### 3. Migraciones y datos existentes

```bash
gcloud run jobs execute gametrack-migrate --project="$PROJECT_ID" --region="$REGION" --wait
```

Para preservar el proyecto actual, detener escrituras de API e importador
locales y generar una copia desde **esta PC**. El script abre el origen en
modo lectura, utiliza la API de backup SQLite y no pisa un archivo existente:

```powershell
backend/.venv-local/Scripts/python.exe deploy/google-cloud/snapshot_sqlite.py `
  --source backend/gametrack.db --output deploy/backups/google-cloud-origen.db
```

Subir esa copia por un canal autenticado al bucket privado, sin ponerla en Git
ni en el contenedor. Ejecutar la copia una sola vez sobre un destino vacío:

```bash
gcloud storage cp RUTA_DE_LA_COPIA "gs://${PROJECT_ID}-gametrack-migration/origen.db" --project="$PROJECT_ID"
gcloud run jobs execute gametrack-import --project="$PROJECT_ID" --region="$REGION" --wait
```

El job valida las tablas y copia en una transacción, conservando IDs y
relaciones. Si falla por un texto histórico incompatible, corregir una copia
revisada siguiendo [DESPLIEGUE.md](../../DESPLIEGUE.md); no borrar el destino
ni truncar datos automáticamente. La transferencia puede tardar más que una
tanda de Steam y tiene un timeout de una hora. Revisar el informe de recuentos
y la integridad antes de continuar. Las copias del bucket de migración tienen
una regla de eliminación después de siete días; conservar el backup local.

Si se decide empezar vacío, omitir el job de importación; eso no conserva los
usuarios, reseñas ni valoraciones existentes. Este despliegue propone conservarlos.

### 4. Entrenar y activar el modelo

Entrenar exclusivamente con observaciones reales. `demo` está prohibido y el
entrenador rechaza perfiles sintéticos y artefactos anteriores sin procedencia
verificada. No cambiar la etiqueta de un artefacto antiguo para activarlo.

```bash
export MODEL_VERSION="observed-$(date -u +%Y%m%d%H%M%S)"
python3 deploy/google-cloud/settings.py --training-version="$MODEL_VERSION" --data-label=observed
terraform -chdir=deploy/google-cloud apply
gcloud run jobs execute gametrack-train --project="$PROJECT_ID" --region="$REGION" --wait
python3 deploy/google-cloud/settings.py --model-version="$MODEL_VERSION"
```

El job escribe primero en disco temporal y publica objetos nuevos mediante
la identidad de Google, sin claves JSON. Rechaza sobrescribir una versión.
Si falla, usar una versión nueva y conservar el modelo activo. La versión de
entrenamiento se separa de la versión servida: no se activa un modelo incompleto.
Si no hay suficientes valoraciones, posponer el entrenamiento y publicar con
`model_version=untrained`: los demás recomendadores siguen disponibles.

### 5. Publicar y verificar

```bash
python3 deploy/google-cloud/settings.py --enable-web
terraform -chdir=deploy/google-cloud plan -out=web.tfplan
terraform -chdir=deploy/google-cloud apply web.tfplan
export PUBLIC_URL="$(terraform -chdir=deploy/google-cloud output -raw public_url)"
curl --fail "$PUBLIC_URL/api/v1/health"
curl --fail "$PUBLIC_URL/"
```

La sonda interna de arranque usa `/healthz`; para comprobaciones públicas
usar `/api/v1/health` y verificar `database=ok`. Cloud Run reserva algunas
rutas terminadas en `z` en su entrada pública ([limitaciones oficiales](https://docs.cloud.google.com/run/docs/known-issues#reserved-url-paths)).

Probar registro/login, permisos por rol, recomendaciones, amigos y login de
Steam. La web reconoce los encabezados del proxy HTTPS de Cloud Run; esa
configuración se aplica sólo a este servicio de la nube. Comparar los datos migrados con los recuentos de origen. Observar
memoria y tiempo de arranque con el catálogo real; el límite web inicial es
1 vCPU/2 GiB, dos instancias y dos conexiones por proceso web. El sincronizador
dispone de tres conexiones para datos, bloqueo de sesión y latido.

Probar una tanda manual y luego habilitar el horario:

```bash
gcloud run jobs execute gametrack-sync --project="$PROJECT_ID" --region="$REGION" --wait
python3 deploy/google-cloud/settings.py --enable-scheduler
terraform -chdir=deploy/google-cloud plan -out=scheduler.tfplan
terraform -chdir=deploy/google-cloud apply scheduler.tfplan
```

Verificar una ejecución programada y que sus resultados aparecen en la web
con la PC apagada. Scheduler confirma el inicio del job; el resultado final
se revisa en las ejecuciones de Cloud Run, no sólo en el historial de Scheduler.

## Actualizaciones y recuperación

El código de esta instalación está en
[Lukitas20/gametrack-tesis](https://github.com/Lukitas20/gametrack-tesis), rama
`feature/IA`. Para trabajar en una copia nueva:

```bash
git clone --branch feature/IA https://github.com/Lukitas20/gametrack-tesis.git
cd gametrack-tesis
```

La publicación es manual: un commit o push no actualiza la web. Cada colaborador
debe autenticarse en Google Cloud con su propia cuenta y tener permisos sobre
`gametrack-510920`. Las herramientas portables y la sesión privada de esta PC
no se distribuyen en Git; usar herramientas propias o Cloud Shell. Para mantener
la instalación existente, reutilizar su estado remoto y conservar sus parámetros
activos antes de aplicar cambios. No repetir la importación inicial de SQLite.

Para actualizar, crear una imagen nueva, guardar su digest con `settings.py`,
aplicar cambios de jobs, ejecutar migraciones controladas y publicar la web.
Evaluar compatibilidad del esquema con la versión web anterior antes de migrar;
si no son compatibles, usar una ventana de mantenimiento. Conservar imágenes
anteriores para volver a un digest conocido y backups para recuperar la base.

Antes de depender de la instalación para producción, ensayar una restauración
en una base de prueba y presupuestar esa instancia temporal. Un cambio de región requiere revisar la migración de Cloud SQL
y buckets; no cambiar la variable sobre una instalación con datos sin planearla.

Los recursos tienen protección contra borrado accidental y los buckets no se
vacían automáticamente. Para cerrar la prueba, exportar datos y planear el
borrado explícito de recursos facturables. Detener la web o pausar Scheduler
no elimina el costo fijo de Cloud SQL. El presupuesto es sólo de alertas y
se calcula excluyendo créditos para mostrar el consumo bruto del piloto.

## Validación local

```bash
terraform -chdir=deploy/google-cloud init -backend=false
terraform -chdir=deploy/google-cloud validate
```

Las pruebas `backend/tests/test_cloud_deployment.py` comprueban backup con WAL,
rechazo de sobrescrituras de backup/modelo y preservación de secretos existentes,
sin acceder a Google Cloud. La validación de Terraform comprueba el esquema del
proveedor; el plan real y las pruebas en la nube requieren la cuenta del titular.

Verificado localmente el 7 de octubre de 2026: formato y validación de Terraform
con el proveedor Google 7.46.1, suite seleccionada de 55 pruebas de configuración,
migraciones, tandas y modelo, y las 13 pruebas finales de despliegue (incluyen
tres casos agregados después de la suite). El entorno Windows requirió ejecutar
las comprobaciones fuera de su aislamiento para permitir acceso a archivos
temporales y al proceso del proveedor. No se usaron credenciales reales,
llamadas a Steam ni servicios de Google en estas pruebas.

Revalidado el 10 de octubre de 2026 antes de sincronizar `feature/IA`: 162 pruebas
de despliegue, autenticación Steam, perfil, API, amistades, reseñas e historial
aprobadas. Formato y validación de Terraform y sintaxis de los cinco módulos
JavaScript modificados también aprobados. La revisión de los archivos preparados
confirmó la exclusión de la clave Steam, credenciales, bases y estado local.
