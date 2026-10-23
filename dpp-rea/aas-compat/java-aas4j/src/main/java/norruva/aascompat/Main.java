package norruva.aascompat;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;

import org.eclipse.digitaltwin.aas4j.v3.dataformat.aasx.AASXDeserializer;
import org.eclipse.digitaltwin.aas4j.v3.dataformat.json.JsonDeserializer;
import org.eclipse.digitaltwin.aas4j.v3.dataformat.json.JsonSerializer;
import org.eclipse.digitaltwin.aas4j.v3.dataformat.xml.XmlDeserializer;
import org.eclipse.digitaltwin.aas4j.v3.model.Environment;

/**
 * Reads every .json/.xml/.aasx file under a corpus directory with AAS4J and prints JSON result rows.
 * With a third argument, also re-serializes each JSON input to that directory for round-trip diffs.
 */
public final class Main {
    public static void main(String[] args) throws Exception {
        Path corpus = Path.of(args[0]);
        String tool = "aas4j-" + args[1];
        Path roundTrip = args.length > 2 ? Path.of(args[2]) : null;
        List<Path> files;
        try (Stream<Path> walk = Files.walk(corpus)) {
            files = walk.filter(Files::isRegularFile)
                    .filter(p -> p.toString().matches(".*\\.(json|xml|aasx)$"))
                    .filter(p -> !p.getFileName().toString().equals("probes-manifest.json"))
                    .sorted().toList();
        }
        List<String> rows = new ArrayList<>();
        for (Path file : files) {
            String status = "pass";
            String detail = "";
            Integer objects = null;
            try (InputStream in = Files.newInputStream(file)) {
                String name = file.getFileName().toString();
                Environment env;
                if (name.endsWith(".json")) {
                    env = new JsonDeserializer().read(in, Environment.class);
                } else if (name.endsWith(".xml")) {
                    env = new XmlDeserializer().read(in);
                } else {
                    env = new AASXDeserializer(in).read();
                }
                objects = env.getAssetAdministrationShells().size() + env.getSubmodels().size()
                        + env.getConceptDescriptions().size();
                if (roundTrip != null && name.endsWith(".json")) {
                    Path out = roundTrip.resolve(corpus.relativize(file));
                    Files.createDirectories(out.getParent());
                    Files.writeString(out, new JsonSerializer().write(env));
                }
            } catch (Throwable error) {
                status = "fail";
                Throwable root = error;
                while (root.getCause() != null && root.getCause() != root) {
                    root = root.getCause();
                }
                detail = error.getClass().getSimpleName() + ": " + String.valueOf(error.getMessage())
                        + (root != error ? " | root " + root.getClass().getSimpleName() + ": " + root.getMessage() : "");
            }
            rows.add("{\"file\":" + quote(corpus.relativize(file).toString()) + ",\"payload\":\"(file)\",\"tool\":"
                    + quote(tool) + ",\"status\":" + quote(status) + ",\"detail\":"
                    + quote(detail.length() > 300 ? detail.substring(0, 300) : detail)
                    + ",\"objects\":" + objects + "}");
        }
        System.out.println("[" + String.join(",\n", rows) + "]");
    }

    private static String quote(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (char c : value.toCharArray()) {
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n', '\r', '\t' -> out.append(' ');
                default -> out.append(c < 0x20 ? ' ' : c);
            }
        }
        return out.append('"').toString();
    }
}
