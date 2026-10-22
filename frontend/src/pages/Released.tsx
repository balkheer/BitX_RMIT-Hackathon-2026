import { useResource } from "../api/client";
import type { RequestSummary } from "../api/types";
import { Empty, ErrorState, Loading, PageHeader } from "../components/ui";
import { RequestTable } from "./Requests";

export function ReleasedPage() {
  const rows = useResource<RequestSummary[]>("/releases");
  return (
    <>
      <PageHeader title="Released data"
        lead="Requests of yours that have approved fields. Open one to view its data; authorisation is re-checked every time." />
      {rows.loading && <Loading />}
      {rows.error && <ErrorState error={rows.error} onRetry={rows.reload} />}
      {rows.data && rows.data.length === 0 && <Empty title="No approved requests yet">Approved requests you make will appear here.</Empty>}
      {rows.data && rows.data.length > 0 && <RequestTable rows={rows.data} showRequester={false} />}
    </>
  );
}
