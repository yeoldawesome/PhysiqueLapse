import { useNavigate } from "react-router";
import GroupPhotos from "../components/GroupPhotos";
import { PageHeader } from "../components/ui";

export default function Grouping() {
  const navigate = useNavigate();
  return (
    <div>
      <PageHeader title="Group photos by pose" subtitle="Photos that align closely are automatically grouped together." />
      <div className="mx-auto flex max-w-6xl flex-col gap-4 p-3 md:p-6">
        <GroupPhotos onApplied={() => navigate("/")} />
      </div>
    </div>
  );
}
