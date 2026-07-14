from __future__ import annotations


SWAGGER_UI_HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>FA Server Swagger</title>
    <link rel="icon" type="image/png" href="/static/swagger-ui/favicon-32x32.png">
    <link rel="stylesheet" href="/static/swagger-ui/swagger-ui.css">
    <style>
      body { margin: 0; background: #fafafa; }
    </style>
  </head>
  <body>
    <div id="swagger-ui"></div>
    <script src="/static/swagger-ui/swagger-ui-bundle.js"></script>
    <script src="/static/swagger-ui/swagger-ui-standalone-preset.js"></script>
    <script>
      window.onload = () => {
        window.ui = SwaggerUIBundle({
          url: "/openapi.json",
          dom_id: "#swagger-ui",
          presets: [
            SwaggerUIBundle.presets.apis,
            SwaggerUIStandalonePreset
          ],
          layout: "StandaloneLayout",
          persistAuthorization: true
        });
      };
    </script>
  </body>
</html>
"""
