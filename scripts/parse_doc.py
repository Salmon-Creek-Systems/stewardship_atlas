#!/usr/bin/env python3
"""
Minimal Proof of Concept: Document Parser for Geospatial Atlas
Extracts keywords and generates GeoJSON from PDF documents using GPT-4.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional
import PyPDF2
import openai
from openai import OpenAI


def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extract text content from a PDF file.
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        Extracted text as string
        
    Raises:
        Exception: If PDF cannot be read or has no extractable text
    """
    try:
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            
            if len(pdf_reader.pages) == 0:
                raise Exception("PDF has no pages")
            
            text_content = ""
            for page_num, page in enumerate(pdf_reader.pages):
                page_text = page.extract_text()
                if page_text:
                    text_content += f"\n--- Page {page_num + 1} ---\n{page_text}\n"
            
            if not text_content.strip():
                raise Exception("No extractable text found in PDF")
                
            return text_content
            
    except Exception as e:
        raise Exception(f"Failed to extract text from PDF: {str(e)}")


def create_gpt_prompt(text_content: str) -> str:
    """
    Create a prompt for GPT-4 to extract keywords and geographic information.
    
    Args:
        text_content: Extracted text from PDF
        
    Returns:
        Formatted prompt string
    """
    return f"""
You are analyzing a document for a geospatial atlas system. Please extract:

1. Up to 10 summary keywords (comma-separated)
2. Geographic references mentioned in the text
3. Any maps or spatial coverage areas described

Document content:
{text_content[:4000]}  # Limit to first 4000 chars to stay within token limits

Please respond with ONLY a valid JSON object in this exact format:
{{
    "keywords": ["keyword1", "keyword2", "keyword3"],
    "geographic_references": [
        {{
            "name": "Place Name",
            "type": "county|town|region|area",
            "description": "Brief description of what this place is"
        }}
    ],
    "map_coverage": [
        {{
            "name": "Map/Area Name",
            "description": "Description of the area covered",
            "spatial_extent": "General description of boundaries or coverage"
        }}
    ]
}}

Important: Respond with ONLY the JSON, no other text or explanation.
"""


def call_gpt4(client: OpenAI, prompt: str) -> Dict[str, Any]:
    """
    Call GPT-4 API to extract information from the document.
    
    Args:
        client: OpenAI client instance
        prompt: Formatted prompt for GPT-4
        
    Returns:
        Parsed JSON response from GPT-4
        
    Raises:
        Exception: If API call fails or response is invalid
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that extracts structured information from documents. Always respond with valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,  # Low temperature for consistent, structured output
            max_tokens=1000
        )
        
        response_text = response.choices[0].message.content.strip()
        
        # Try to parse the JSON response
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            raise Exception(f"GPT-4 returned invalid JSON: {response_text[:200]}... Error: {str(e)}")
            
    except Exception as e:
        raise Exception(f"GPT-4 API call failed: {str(e)}")


def generate_geojson(gpt_response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate GeoJSON FeatureCollection from GPT-4 extracted geographic information.
    
    Args:
        gpt_response: Parsed response from GPT-4
        
    Returns:
        GeoJSON FeatureCollection as dictionary
    """
    features = []
    
    # Process geographic references
    for ref in gpt_response.get('geographic_references', []):
        # Create a simple bounding box for each place
        # This is a very rough approximation - in practice you'd want proper geocoding
        feature = {
            "type": "Feature",
            "properties": {
                "name": ref.get('name', 'Unknown'),
                "type": ref.get('type', 'unknown'),
                "description": ref.get('description', ''),
                "source": "text_reference"
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-180, -90],  # Very rough bounding box
                    [180, -90],
                    [180, 90],
                    [-180, 90],
                    [-180, -90]
                ]]
            }
        }
        features.append(feature)
    
    # Process map coverage areas
    for coverage in gpt_response.get('map_coverage', []):
        feature = {
            "type": "Feature",
            "properties": {
                "name": coverage.get('name', 'Unknown'),
                "description": coverage.get('description', ''),
                "spatial_extent": coverage.get('spatial_extent', ''),
                "source": "map_coverage"
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-180, -90],  # Very rough bounding box
                    [180, -90],
                    [180, 90],
                    [-180, 90],
                    [-180, -90]
                ]]
            }
        }
        features.append(feature)
    
    return {
        "type": "FeatureCollection",
        "crs": {
            "type": "name",
            "properties": {
                "name": "EPSG:4326"
            }
        },
        "features": features
    }


def parse_document(pdf_path: str, api_key: str) -> Dict[str, Any]:
    """
    Main function to parse a PDF document and extract structured information.
    
    Args:
        pdf_path: Path to the PDF file
        api_key: OpenAI API key
        
    Returns:
        Dictionary containing keywords and GeoJSON
    """
    try:
        # Initialize OpenAI client
        client = OpenAI(api_key=api_key)
        
        # Extract text from PDF
        print(f"Extracting text from: {pdf_path}")
        text_content = extract_text_from_pdf(pdf_path)
        print(f"Extracted {len(text_content)} characters of text")
        
        # Create GPT-4 prompt
        prompt = create_gpt_prompt(text_content)
        
        # Call GPT-4
        print("Calling GPT-4 for analysis...")
        gpt_response = call_gpt4(client, prompt)
        print("GPT-4 analysis complete")
        
        # Generate GeoJSON
        geojson = generate_geojson(gpt_response)
        
        # Prepare final output
        result = {
            "keywords": gpt_response.get('keywords', []),
            "geojson": geojson,
            "metadata": {
                "source_file": pdf_path,
                "extraction_method": "gpt4_analysis",
                "text_length": len(text_content),
                "features_extracted": len(geojson['features'])
            }
        }
        
        return result
        
    except Exception as e:
        return {
            "error": str(e),
            "error_type": type(e).__name__,
            "source_file": pdf_path
        }


def main():
    """Main entry point for command line usage."""
    parser = argparse.ArgumentParser(description='Parse PDF documents for geospatial atlas')
    parser.add_argument('pdf_path', help='Path to the PDF file to parse')
    parser.add_argument('--api-key', help='OpenAI API key (or set OPENAI_API_KEY env var)')
    parser.add_argument('--output', '-o', help='Output JSON file (default: stdout)')
    
    args = parser.parse_args()
    
    # Get API key
    api_key = args.api_key or os.getenv('OPENAI_API_KEY')
    if not api_key:
        print("Error: OpenAI API key required. Set OPENAI_API_KEY environment variable or use --api-key")
        sys.exit(1)
    
    # Check if PDF exists
    if not os.path.exists(args.pdf_path):
        print(f"Error: PDF file not found: {args.pdf_path}")
        sys.exit(1)
    
    # Parse document
    result = parse_document(args.pdf_path, api_key)
    
    # Output results
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Results saved to: {args.output}")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main() 