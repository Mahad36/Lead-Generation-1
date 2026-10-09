"use client";

import { TemplateEditor } from "@/components/template-editor";
import { useParams } from "next/navigation";

export default function EditTemplatePage() {
  const { id } = useParams<{ id: string }>();
  return <TemplateEditor id={id} />;
}
