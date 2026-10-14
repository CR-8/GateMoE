import { Toaster as Sonner, type ToasterProps } from "sonner";

function Toaster(props: ToasterProps) {
  return (
    <Sonner position="bottom-center"
      toastOptions={{ classNames: { toast: "!rounded-full !border-0 !bg-graphite !text-white !px-5 !py-3 !text-body-sm",
                                   description: "!text-white/80" } }}
      {...props} />
  );
}

export { Toaster };
