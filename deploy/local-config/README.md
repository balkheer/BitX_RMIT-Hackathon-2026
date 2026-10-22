Put the `tidecloak.json` you download from tide-console in this folder
(Apps → your app → Credentials → Tide adapter → Download tidecloak.json).

The local Docker stack serves it to the browser at http://localhost:5173/tidecloak.json and the API reads it
from here. After adding or replacing it, run:

    docker compose -f deploy/docker-compose.local.yml restart api
